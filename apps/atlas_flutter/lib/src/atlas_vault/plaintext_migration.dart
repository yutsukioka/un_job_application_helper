import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import '../../atlas.dart';
import '../../atlas_vault.dart' as vault;
import 'canonical_json.dart';
import 'local_store_io.dart';
import 'strict_values.dart';

enum AtlasVaultPlaintextMigrationStage {
  prepared('prepared'),
  encryptedVerified('encrypted_verified'),
  commitInProgress('commit_in_progress'),
  plaintextRemoved('plaintext_removed'),
  selectionCommitted('selection_committed'),
  completionPending('completion_pending');

  const AtlasVaultPlaintextMigrationStage(this.wireName);

  final String wireName;

  static AtlasVaultPlaintextMigrationStage parse(Object? value) {
    if (value is String) {
      for (final stage in values) {
        if (stage.wireName == value) {
          return stage;
        }
      }
    }
    throw const AtlasVaultPlaintextMigrationException();
  }
}

enum AtlasVaultPlaintextAuthorityState {
  unresolved,
  legacy,
  migrationPending,
  encryptedSelectedInactive,
  encryptedActive,
  recoveryRequired,
  unsupported,
}

final class AtlasVaultPlaintextMigrationException implements Exception {
  const AtlasVaultPlaintextMigrationException();

  @override
  String toString() => 'AtlasVault plaintext migration operation failed.';
}

final class AtlasVaultPlaintextPrivateState {
  AtlasVaultPlaintextPrivateState({
    required List<AtlasSavedSearch> savedSearches,
    required List<AtlasApplicationRecord> trackerRecords,
  }) : savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches),
       trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         trackerRecords,
       );

  final List<AtlasSavedSearch> savedSearches;
  final List<AtlasApplicationRecord> trackerRecords;

  @override
  String toString() => 'AtlasVaultPlaintextPrivateState(<redacted>)';
}

final class AtlasVaultRemoteTrackerHandle {
  AtlasVaultRemoteTrackerHandle({
    required this.recordId,
    required this.jobKey,
  }) {
    if (recordId.isEmpty || jobKey.isEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  final String recordId;
  final String jobKey;

  factory AtlasVaultRemoteTrackerHandle.fromJson(Map<String, Object?> source) {
    _requireExactKeys(source, const <String>{'record_id', 'job_key'});
    return AtlasVaultRemoteTrackerHandle(
      recordId: _requiredText(source['record_id']),
      jobKey: _requiredText(source['job_key']),
    );
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'record_id': recordId,
    'job_key': jobKey,
  };

  @override
  String toString() => 'AtlasVaultRemoteTrackerHandle(<redacted>)';
}

final class AtlasVaultPlaintextMigrationSummary {
  const AtlasVaultPlaintextMigrationSummary({
    required this.savedSearchCount,
    required this.trackerRecordCount,
    required this.localCachePrivatePresent,
    required this.compatibilityPrivatePresent,
    this.stage,
  });

  final int savedSearchCount;
  final int trackerRecordCount;
  final bool localCachePrivatePresent;
  final bool compatibilityPrivatePresent;
  final AtlasVaultPlaintextMigrationStage? stage;

  @override
  String toString() => 'AtlasVaultPlaintextMigrationSummary(<redacted>)';
}

abstract interface class AtlasVaultPlaintextMigrationPrivateAuthority {
  bool get isEncryptedPrivateStateActive;

  void hideLegacyPrivateState();

  Future<bool> activateEncryptedPrivateState(String vaultId);

  Future<AtlasVaultPlaintextPrivateState> readEncryptedPrivateState();
}

abstract interface class AtlasVaultPlaintextMigrationCoordinating {
  Future<AtlasVaultPlaintextAuthorityState> inspectAuthority();

  Future<AtlasVaultPlaintextMigrationSummary> inventory();

  Future<AtlasVaultPlaintextMigrationSummary> prepare();

  Future<void> discardPrepared();

  Future<AtlasVaultPlaintextMigrationSummary> finalizeAndActivate();

  Future<AtlasVaultPlaintextMigrationSummary> resume();

  Future<AtlasVaultPlaintextMigrationSummary> activateSelected();
}

abstract interface class AtlasVaultPlaintextMigrationOperationAdmission {
  Future<void> drainAdmittedPlaintextOperations();
}

final class _NoopMigrationOperationAdmission
    implements AtlasVaultPlaintextMigrationOperationAdmission {
  const _NoopMigrationOperationAdmission();

  @override
  Future<void> drainAdmittedPlaintextOperations() async {}
}

abstract interface class AtlasVaultPlaintextStateSource {
  Future<AtlasVaultPlaintextPrivateState> readPlaintextPrivateState();
}

abstract interface class AtlasVaultCompatibilityPrivateSource {
  Uri get authorityBaseURL;

  Future<AtlasVaultPlaintextPrivateState> readCompatibilityPrivateState();

  Future<bool> deleteSavedSearch(String name);

  Future<bool> deleteTrackerRecord(String recordId);
}

abstract interface class AtlasLocalCacheMigrationSource {
  Future<AtlasLocalCacheMigrationPrivateState> readPrivateStateForMigration();

  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  });
}

final class AtlasLocalCacheMigrationStoreSource
    implements AtlasLocalCacheMigrationSource {
  const AtlasLocalCacheMigrationStoreSource(this.store);

  final AtlasLocalCacheStore store;

  @override
  Future<AtlasLocalCacheMigrationPrivateState> readPrivateStateForMigration() {
    return store.readPrivateStateForMigration();
  }

  @override
  Future<void> removePrivateStateForMigration({
    required String expectedPrivateSha256,
  }) {
    return store.removePrivateStateForMigration(
      expectedPrivateSha256: expectedPrivateSha256,
    );
  }

  @override
  String toString() => 'AtlasLocalCacheMigrationStoreSource(<redacted>)';
}

abstract interface class AtlasVaultProtectedMigrationJournalStore {
  Future<Uint8List?> read();

  Future<void> create(Uint8List canonicalBytes);

  Future<void> replace(
    Uint8List canonicalBytes, {
    required String expectedSha256,
  });

  Future<void> delete({
    required String expectedSha256,
    bool allowAbsent = false,
  });
}

abstract interface class AtlasVaultSelectedVaultStore {
  Future<String?> read();

  Future<void> create(String vaultId);

  Future<void> clear(String expectedVaultId);
}

abstract interface class AtlasVaultMigrationSecureKeyStore {
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey);

  Future<Uint8List?> loadVaultKey(String vaultId);

  Future<bool> containsVaultKey(String vaultId);

  Future<void> deleteVaultKey(String vaultId);
}

final class AtlasVaultPlaintextMigrationJournal {
  AtlasVaultPlaintextMigrationJournal._({
    required this.migrationId,
    required this.stage,
    required this.vaultId,
    required this.storeId,
    required this.createdAt,
    required this.inventorySha256,
    required List<AtlasSavedSearch> savedSearches,
    required List<AtlasApplicationRecord> trackerRecords,
    required List<AtlasSavedSearch> remoteSavedSearches,
    required List<AtlasApplicationRecord> remoteTrackerRecords,
    required List<String> remoteSavedSearchNames,
    required List<AtlasVaultRemoteTrackerHandle> remoteTrackerHandles,
    required this.compatibilityAuthority,
    required this.cachePrivateSha256,
    required this.vaultKeySha256,
    required this.storeSha256,
    required List<String> deletedSavedSearchNames,
    required List<String> deletedTrackerRecordIds,
    required this.cacheCleared,
    required this.selectionCreated,
    required this.rollbackStarted,
    required this.rollbackStoreDeleted,
  }) : savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches),
       trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         trackerRecords,
       ),
       remoteSavedSearches = List<AtlasSavedSearch>.unmodifiable(
         remoteSavedSearches,
       ),
       remoteTrackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         remoteTrackerRecords,
       ),
       remoteSavedSearchNames = List<String>.unmodifiable(
         remoteSavedSearchNames,
       ),
       remoteTrackerHandles = List<AtlasVaultRemoteTrackerHandle>.unmodifiable(
         remoteTrackerHandles,
       ),
       deletedSavedSearchNames = List<String>.unmodifiable(
         deletedSavedSearchNames,
       ),
       deletedTrackerRecordIds = List<String>.unmodifiable(
         deletedTrackerRecordIds,
       ) {
    _validate();
  }

  static const format = 'atlasvault-android-plaintext-migration';
  static const version = 1;

  final String migrationId;
  final AtlasVaultPlaintextMigrationStage stage;
  final String vaultId;
  final String storeId;
  final String createdAt;
  final String inventorySha256;
  final List<AtlasSavedSearch> savedSearches;
  final List<AtlasApplicationRecord> trackerRecords;
  final List<AtlasSavedSearch> remoteSavedSearches;
  final List<AtlasApplicationRecord> remoteTrackerRecords;
  final List<String> remoteSavedSearchNames;
  final List<AtlasVaultRemoteTrackerHandle> remoteTrackerHandles;
  final String compatibilityAuthority;
  final String? cachePrivateSha256;
  final String? vaultKeySha256;
  final String? storeSha256;
  final List<String> deletedSavedSearchNames;
  final List<String> deletedTrackerRecordIds;
  final bool cacheCleared;
  final bool selectionCreated;
  final bool rollbackStarted;
  final bool rollbackStoreDeleted;

  factory AtlasVaultPlaintextMigrationJournal._prepared({
    required String migrationId,
    required String vaultId,
    required String storeId,
    required String createdAt,
    required _MigrationInventory inventory,
  }) {
    return AtlasVaultPlaintextMigrationJournal._(
      migrationId: migrationId,
      stage: AtlasVaultPlaintextMigrationStage.prepared,
      vaultId: vaultId,
      storeId: storeId,
      createdAt: createdAt,
      inventorySha256: inventory.sha256,
      savedSearches: inventory.savedSearches,
      trackerRecords: inventory.trackerRecords,
      remoteSavedSearches: inventory.remoteSavedSearches,
      remoteTrackerRecords: inventory.remoteTrackerRecords,
      remoteSavedSearchNames: inventory.remoteSavedSearchNames,
      remoteTrackerHandles: inventory.remoteTrackerHandles,
      compatibilityAuthority: inventory.compatibilityAuthority,
      cachePrivateSha256: inventory.cachePrivateSha256,
      vaultKeySha256: null,
      storeSha256: null,
      deletedSavedSearchNames: const <String>[],
      deletedTrackerRecordIds: const <String>[],
      cacheCleared: false,
      selectionCreated: false,
      rollbackStarted: false,
      rollbackStoreDeleted: false,
    );
  }

  factory AtlasVaultPlaintextMigrationJournal.decodeBytes(Uint8List bytes) {
    if (bytes.isEmpty || bytes.length > 16 * 1024 * 1024) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    try {
      return AtlasVaultPlaintextMigrationJournal.fromJson(
        requireAtlasVaultObject(
          jsonDecode(utf8.decode(bytes, allowMalformed: false)),
          context: 'Migration journal',
        ),
      );
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  factory AtlasVaultPlaintextMigrationJournal.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      _requireExactKeys(value, const <String>{
        'format',
        'version',
        'migration_id',
        'stage',
        'vault_id',
        'store_id',
        'created_at',
        'inventory_sha256',
        'saved_searches',
        'tracker_records',
        'remote_saved_searches',
        'remote_tracker_records',
        'remote_saved_search_names',
        'remote_tracker_handles',
        'compatibility_authority',
        'cache_private_sha256',
        'vault_key_sha256',
        'store_sha256',
        'deleted_saved_search_names',
        'deleted_tracker_record_ids',
        'cache_cleared',
        'selection_created',
        'rollback_started',
        'rollback_store_deleted',
      });
      if (value['format'] != format ||
          requireAtlasVaultInt(value['version'], field: 'migration.version') !=
              version) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final savedSearches = <AtlasSavedSearch>[
        for (final item in _requiredList(value['saved_searches']))
          _savedSearchFromJournal(_stringMap(item)),
      ];
      final trackerRecords = <AtlasApplicationRecord>[
        for (final item in _requiredList(value['tracker_records']))
          _trackerFromJournal(_stringMap(item)),
      ];
      final remoteSavedSearches = <AtlasSavedSearch>[
        for (final item in _requiredList(value['remote_saved_searches']))
          _savedSearchFromJournal(_stringMap(item)),
      ];
      final remoteTrackerRecords = <AtlasApplicationRecord>[
        for (final item in _requiredList(value['remote_tracker_records']))
          _trackerFromJournal(_stringMap(item)),
      ];
      final remoteSavedSearchNames = <String>[
        for (final item in _requiredList(value['remote_saved_search_names']))
          _requiredText(item),
      ];
      final remoteTrackerHandles = <AtlasVaultRemoteTrackerHandle>[
        for (final item in _requiredList(value['remote_tracker_handles']))
          AtlasVaultRemoteTrackerHandle.fromJson(_stringMap(item)),
      ];
      final deletedSavedSearchNames = <String>[
        for (final item in _requiredList(value['deleted_saved_search_names']))
          _requiredText(item),
      ];
      final deletedTrackerRecordIds = <String>[
        for (final item in _requiredList(value['deleted_tracker_record_ids']))
          _requiredText(item),
      ];
      return AtlasVaultPlaintextMigrationJournal._(
        migrationId: requireAtlasVaultCanonicalUuid(
          value['migration_id'],
          field: 'migration.migration_id',
        ),
        stage: AtlasVaultPlaintextMigrationStage.parse(value['stage']),
        vaultId: requireAtlasVaultVaultId(value['vault_id']),
        storeId: requireAtlasVaultCanonicalUuid(
          value['store_id'],
          field: 'migration.store_id',
        ),
        createdAt: requireAtlasVaultUtcSeconds(
          value['created_at'],
          field: 'migration.created_at',
        ),
        inventorySha256: _requiredSha256(value['inventory_sha256']),
        savedSearches: savedSearches,
        trackerRecords: trackerRecords,
        remoteSavedSearches: remoteSavedSearches,
        remoteTrackerRecords: remoteTrackerRecords,
        remoteSavedSearchNames: remoteSavedSearchNames,
        remoteTrackerHandles: remoteTrackerHandles,
        compatibilityAuthority: _requiredCompatibilityAuthority(
          value['compatibility_authority'],
        ),
        cachePrivateSha256: _optionalSha256(value['cache_private_sha256']),
        vaultKeySha256: _optionalSha256(value['vault_key_sha256']),
        storeSha256: _optionalSha256(value['store_sha256']),
        deletedSavedSearchNames: deletedSavedSearchNames,
        deletedTrackerRecordIds: deletedTrackerRecordIds,
        cacheCleared: requireAtlasVaultBool(
          value['cache_cleared'],
          field: 'migration.cache_cleared',
        ),
        selectionCreated: requireAtlasVaultBool(
          value['selection_created'],
          field: 'migration.selection_created',
        ),
        rollbackStarted: requireAtlasVaultBool(
          value['rollback_started'],
          field: 'migration.rollback_started',
        ),
        rollbackStoreDeleted: requireAtlasVaultBool(
          value['rollback_store_deleted'],
          field: 'migration.rollback_store_deleted',
        ),
      );
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  AtlasVaultPlaintextMigrationJournal withResourceHashes({
    String? vaultKeySha256,
    String? storeSha256,
    AtlasVaultPlaintextMigrationStage? stage,
  }) {
    return _copy(
      stage: stage,
      vaultKeySha256: vaultKeySha256,
      preserveVaultKeySha256: vaultKeySha256 == null,
      storeSha256: storeSha256,
      preserveStoreSha256: storeSha256 == null,
    );
  }

  AtlasVaultPlaintextMigrationJournal transitionedTo(
    AtlasVaultPlaintextMigrationStage next,
  ) {
    if (rollbackStarted || next.index != stage.index + 1) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return _copy(stage: next);
  }

  AtlasVaultPlaintextMigrationJournal withRollbackProgress({
    bool? rollbackStarted,
    bool? rollbackStoreDeleted,
  }) {
    final nextStarted = rollbackStarted ?? this.rollbackStarted;
    final nextStoreDeleted = rollbackStoreDeleted ?? this.rollbackStoreDeleted;
    if ((this.rollbackStarted && !nextStarted) ||
        (this.rollbackStoreDeleted && !nextStoreDeleted) ||
        (!nextStarted && nextStoreDeleted) ||
        stage.index >=
            AtlasVaultPlaintextMigrationStage.commitInProgress.index) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return _copy(
      rollbackStarted: nextStarted,
      rollbackStoreDeleted: nextStoreDeleted,
    );
  }

  AtlasVaultPlaintextMigrationJournal withCommitProgress({
    List<String>? deletedSavedSearchNames,
    List<String>? deletedTrackerRecordIds,
    bool? cacheCleared,
    bool? selectionCreated,
    AtlasVaultPlaintextMigrationStage? stage,
  }) {
    if (stage != null &&
        stage != this.stage &&
        stage.index != this.stage.index + 1) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return _copy(
      stage: stage,
      deletedSavedSearchNames: deletedSavedSearchNames,
      deletedTrackerRecordIds: deletedTrackerRecordIds,
      cacheCleared: cacheCleared,
      selectionCreated: selectionCreated,
    );
  }

  AtlasVaultPlaintextMigrationJournal _copy({
    AtlasVaultPlaintextMigrationStage? stage,
    String? vaultKeySha256,
    bool preserveVaultKeySha256 = true,
    String? storeSha256,
    bool preserveStoreSha256 = true,
    List<String>? deletedSavedSearchNames,
    List<String>? deletedTrackerRecordIds,
    bool? cacheCleared,
    bool? selectionCreated,
    bool? rollbackStarted,
    bool? rollbackStoreDeleted,
  }) {
    return AtlasVaultPlaintextMigrationJournal._(
      migrationId: migrationId,
      stage: stage ?? this.stage,
      vaultId: vaultId,
      storeId: storeId,
      createdAt: createdAt,
      inventorySha256: inventorySha256,
      savedSearches: savedSearches,
      trackerRecords: trackerRecords,
      remoteSavedSearches: remoteSavedSearches,
      remoteTrackerRecords: remoteTrackerRecords,
      remoteSavedSearchNames: remoteSavedSearchNames,
      remoteTrackerHandles: remoteTrackerHandles,
      compatibilityAuthority: compatibilityAuthority,
      cachePrivateSha256: cachePrivateSha256,
      vaultKeySha256: preserveVaultKeySha256
          ? this.vaultKeySha256
          : vaultKeySha256,
      storeSha256: preserveStoreSha256 ? this.storeSha256 : storeSha256,
      deletedSavedSearchNames:
          deletedSavedSearchNames ?? this.deletedSavedSearchNames,
      deletedTrackerRecordIds:
          deletedTrackerRecordIds ?? this.deletedTrackerRecordIds,
      cacheCleared: cacheCleared ?? this.cacheCleared,
      selectionCreated: selectionCreated ?? this.selectionCreated,
      rollbackStarted: rollbackStarted ?? this.rollbackStarted,
      rollbackStoreDeleted: rollbackStoreDeleted ?? this.rollbackStoreDeleted,
    );
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': format,
    'version': version,
    'migration_id': migrationId,
    'stage': stage.wireName,
    'vault_id': vaultId,
    'store_id': storeId,
    'created_at': createdAt,
    'inventory_sha256': inventorySha256,
    'saved_searches': <Object?>[
      for (final value in savedSearches) _savedSearchJson(value),
    ],
    'tracker_records': <Object?>[
      for (final value in trackerRecords) _trackerJson(value),
    ],
    'remote_saved_searches': <Object?>[
      for (final value in remoteSavedSearches) _savedSearchJson(value),
    ],
    'remote_tracker_records': <Object?>[
      for (final value in remoteTrackerRecords) _trackerJson(value),
    ],
    'remote_saved_search_names': List<String>.from(remoteSavedSearchNames),
    'remote_tracker_handles': <Object?>[
      for (final value in remoteTrackerHandles) value.toJson(),
    ],
    'compatibility_authority': compatibilityAuthority,
    'cache_private_sha256': cachePrivateSha256,
    'vault_key_sha256': vaultKeySha256,
    'store_sha256': storeSha256,
    'deleted_saved_search_names': List<String>.from(deletedSavedSearchNames),
    'deleted_tracker_record_ids': List<String>.from(deletedTrackerRecordIds),
    'cache_cleared': cacheCleared,
    'selection_created': selectionCreated,
    'rollback_started': rollbackStarted,
    'rollback_store_deleted': rollbackStoreDeleted,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  void _validate() {
    requireAtlasVaultCanonicalUuid(
      migrationId,
      field: 'migration.migration_id',
    );
    requireAtlasVaultVaultId(vaultId);
    requireAtlasVaultCanonicalUuid(storeId, field: 'migration.store_id');
    requireAtlasVaultUtcSeconds(createdAt, field: 'migration.created_at');
    _requiredCompatibilityAuthority(compatibilityAuthority);
    _requiredSha256(inventorySha256);
    _optionalSha256(cachePrivateSha256);
    _optionalSha256(vaultKeySha256);
    _optionalSha256(storeSha256);
    _requireSortedUnique(
      savedSearches.map((value) => value.name).toList(growable: false),
    );
    _requireSortedUnique(
      trackerRecords.map((value) => value.jobKey).toList(growable: false),
    );
    final remoteSearchNames = remoteSavedSearches
        .map((value) => value.name)
        .toList(growable: false);
    _requireSortedUnique(remoteSearchNames);
    final remoteTrackerKeys = remoteTrackerRecords
        .map((value) => '${value.jobKey}\u0000${value.id}')
        .toList(growable: false);
    _requireSortedUnique(remoteTrackerKeys);
    _requireSortedUnique(remoteSavedSearchNames);
    _requireSortedUnique(deletedSavedSearchNames);
    _requireSortedUnique(deletedTrackerRecordIds);
    final trackerKeys = remoteTrackerHandles
        .map((value) => '${value.jobKey}\u0000${value.recordId}')
        .toList(growable: false);
    _requireSortedUnique(trackerKeys);
    if (remoteTrackerHandles.map((value) => value.recordId).toSet().length !=
        remoteTrackerHandles.length) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (!_jsonEqual(remoteSearchNames, remoteSavedSearchNames) ||
        !_jsonEqual(
          <Object?>[
            for (final value in remoteTrackerRecords)
              AtlasVaultRemoteTrackerHandle(
                recordId: value.id,
                jobKey: value.jobKey,
              ).toJson(),
          ],
          <Object?>[for (final value in remoteTrackerHandles) value.toJson()],
        )) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (!remoteSavedSearchNames.toSet().containsAll(deletedSavedSearchNames) ||
        !remoteTrackerHandles
            .map((value) => value.recordId)
            .toSet()
            .containsAll(deletedTrackerRecordIds)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (stage.index >=
            AtlasVaultPlaintextMigrationStage.encryptedVerified.index &&
        (vaultKeySha256 == null || storeSha256 == null)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (cacheCleared &&
        stage.index <
            AtlasVaultPlaintextMigrationStage.commitInProgress.index) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if ((deletedSavedSearchNames.isNotEmpty ||
            deletedTrackerRecordIds.isNotEmpty) &&
        stage.index <
            AtlasVaultPlaintextMigrationStage.commitInProgress.index) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (selectionCreated &&
        stage.index <
            AtlasVaultPlaintextMigrationStage.selectionCommitted.index) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (rollbackStoreDeleted && !rollbackStarted) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (rollbackStarted &&
        (stage.index >=
                AtlasVaultPlaintextMigrationStage.commitInProgress.index ||
            deletedSavedSearchNames.isNotEmpty ||
            deletedTrackerRecordIds.isNotEmpty ||
            cacheCleared ||
            selectionCreated)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (stage.index >=
            AtlasVaultPlaintextMigrationStage.plaintextRemoved.index &&
        (!cacheCleared ||
            deletedSavedSearchNames.length != remoteSavedSearchNames.length ||
            deletedTrackerRecordIds.length != remoteTrackerHandles.length)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (stage.index >=
            AtlasVaultPlaintextMigrationStage.selectionCommitted.index &&
        !selectionCreated) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  @override
  String toString() => 'AtlasVaultPlaintextMigrationJournal(<redacted>)';
}

final class AtlasVaultPlaintextMigrationCoordinator
    implements AtlasVaultPlaintextMigrationCoordinating {
  AtlasVaultPlaintextMigrationCoordinator({
    required AtlasVaultPlaintextStateSource inMemorySource,
    required AtlasVaultCompatibilityPrivateSource compatibilitySource,
    required AtlasLocalCacheMigrationSource cacheSource,
    AtlasVaultPlaintextMigrationOperationAdmission? operationAdmission,
    required AtlasVaultProtectedMigrationJournalStore journalStore,
    required AtlasVaultSelectedVaultStore selectedVaultStore,
    required AtlasVaultMigrationSecureKeyStore secureKeyStore,
    required AtlasVaultLocalStoreIO localStoreIO,
    required AtlasVaultPlaintextMigrationPrivateAuthority privateAuthority,
    DateTime Function()? now,
    String Function()? uuidProvider,
    Uint8List Function()? vaultKeyProvider,
    Uint8List Function()? nonceProvider,
  }) : // Keep public constructor parameter names stable.
       // ignore: prefer_initializing_formals
       _inMemorySource = inMemorySource,
       // ignore: prefer_initializing_formals
       _compatibilitySource = compatibilitySource,
       // ignore: prefer_initializing_formals
       _cacheSource = cacheSource,
       _operationAdmission =
           operationAdmission ?? const _NoopMigrationOperationAdmission(),
       // ignore: prefer_initializing_formals
       _journalStore = journalStore,
       // ignore: prefer_initializing_formals
       _selectedVaultStore = selectedVaultStore,
       // ignore: prefer_initializing_formals
       _secureKeyStore = secureKeyStore,
       // ignore: prefer_initializing_formals
       _localStoreIO = localStoreIO,
       // ignore: prefer_initializing_formals
       _privateAuthority = privateAuthority,
       _now = now ?? DateTime.now,
       _uuidProvider = uuidProvider ?? _secureUuidV4,
       _vaultKeyProvider = vaultKeyProvider ?? _secureVaultKey,
       _nonceProvider = nonceProvider ?? _secureNonce;

  static const _recordKeyId = 'primary-android-local-key-v1';

  final AtlasVaultPlaintextStateSource _inMemorySource;
  final AtlasVaultCompatibilityPrivateSource _compatibilitySource;
  final AtlasLocalCacheMigrationSource _cacheSource;
  final AtlasVaultPlaintextMigrationOperationAdmission _operationAdmission;
  final AtlasVaultProtectedMigrationJournalStore _journalStore;
  final AtlasVaultSelectedVaultStore _selectedVaultStore;
  final AtlasVaultMigrationSecureKeyStore _secureKeyStore;
  final AtlasVaultLocalStoreIO _localStoreIO;
  final AtlasVaultPlaintextMigrationPrivateAuthority _privateAuthority;
  final DateTime Function() _now;
  final String Function() _uuidProvider;
  final Uint8List Function() _vaultKeyProvider;
  final Uint8List Function() _nonceProvider;

  _MigrationInventory? _reviewedInventory;
  bool _operating = false;

  @override
  Future<AtlasVaultPlaintextAuthorityState> inspectAuthority() async {
    try {
      return await _serialized(() async {
        final journalBytes = await _journalStore.read();
        AtlasVaultPlaintextMigrationJournal? journal;
        try {
          if (journalBytes != null) {
            journal = AtlasVaultPlaintextMigrationJournal.decodeBytes(
              journalBytes,
            );
          }
        } finally {
          _wipe(journalBytes);
        }
        final selected = await _selectedVaultStore.read();
        if (journal != null) {
          final selectionRequired =
              journal.stage.index >=
              AtlasVaultPlaintextMigrationStage.selectionCommitted.index;
          final interruptedSelectionMayExist =
              journal.stage ==
              AtlasVaultPlaintextMigrationStage.plaintextRemoved;
          if ((selectionRequired && selected != journal.vaultId) ||
              (!selectionRequired &&
                  !interruptedSelectionMayExist &&
                  selected != null) ||
              (interruptedSelectionMayExist &&
                  selected != null &&
                  selected != journal.vaultId)) {
            return AtlasVaultPlaintextAuthorityState.recoveryRequired;
          }
          return AtlasVaultPlaintextAuthorityState.migrationPending;
        }
        if (selected != null) {
          return _privateAuthority.isEncryptedPrivateStateActive
              ? AtlasVaultPlaintextAuthorityState.encryptedActive
              : AtlasVaultPlaintextAuthorityState.encryptedSelectedInactive;
        }
        if (_privateAuthority.isEncryptedPrivateStateActive) {
          return AtlasVaultPlaintextAuthorityState.recoveryRequired;
        }
        return AtlasVaultPlaintextAuthorityState.legacy;
      });
    } catch (_) {
      return AtlasVaultPlaintextAuthorityState.recoveryRequired;
    }
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> inventory() async {
    return _serialized(() async {
      final inventory = await _readInventory();
      _reviewedInventory = inventory;
      return inventory.summary();
    });
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> prepare() async {
    return _serialized(() async {
      try {
        final reviewed = _reviewedInventory;
        if (reviewed == null ||
            await _selectedVaultStore.read() != null ||
            await _journalStore.read() != null) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        final current = await _readInventory();
        if (!_sameInventory(reviewed, current)) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        final journal = AtlasVaultPlaintextMigrationJournal._prepared(
          migrationId: _nextUuid('migration.migration_id'),
          vaultId: _nextUuid('vault.vault_id'),
          storeId: _nextUuid('migration.store_id'),
          createdAt: _utcSeconds(_now()),
          inventory: current,
        );
        final bytes = journal.canonicalBytes();
        try {
          await _journalStore.create(bytes);
        } finally {
          _wipe(bytes);
        }
        return await _continuePreparation(journal);
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  Future<AtlasVaultPlaintextMigrationSummary> resumePreparation() async {
    return _serialized(() async {
      try {
        final journal = await _readJournalRequired();
        if (journal.rollbackStarted) {
          return await _continueRollback(journal);
        }
        if (journal.stage ==
            AtlasVaultPlaintextMigrationStage.encryptedVerified) {
          await _verifyPreparedResources(journal);
          return _summaryFromJournal(journal);
        }
        if (journal.stage != AtlasVaultPlaintextMigrationStage.prepared ||
            await _selectedVaultStore.read() != null) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        return await _continuePreparation(journal);
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  @override
  Future<void> discardPrepared() async {
    await _serialized(() async {
      try {
        var journal = await _readJournalRequired();
        if (journal.stage != AtlasVaultPlaintextMigrationStage.prepared &&
            journal.stage !=
                AtlasVaultPlaintextMigrationStage.encryptedVerified) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        if (journal.deletedSavedSearchNames.isNotEmpty ||
            journal.deletedTrackerRecordIds.isNotEmpty ||
            journal.cacheCleared ||
            journal.selectionCreated ||
            await _selectedVaultStore.read() != null) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        if (!journal.rollbackStarted) {
          final before = await _readInventory();
          if (!_journalMatchesInventory(journal, before)) {
            throw const AtlasVaultPlaintextMigrationException();
          }

          String? keyDigest;
          final key = await _secureKeyStore.loadVaultKey(journal.vaultId);
          try {
            if (key != null) {
              if (key.length != 32) {
                throw const AtlasVaultPlaintextMigrationException();
              }
              keyDigest = await vault.atlasVaultSha256Hex(key);
              if (journal.vaultKeySha256 != null &&
                  journal.vaultKeySha256 != keyDigest) {
                throw const AtlasVaultPlaintextMigrationException();
              }
            } else if (journal.vaultKeySha256 != null) {
              throw const AtlasVaultPlaintextMigrationException();
            }
          } finally {
            _wipe(key);
          }

          String? storeDigest;
          final store = await _localStoreIO.read(journal.vaultId);
          if (store != null) {
            storeDigest = await _verifyStore(journal: journal, store: store);
          } else if (journal.storeSha256 != null) {
            throw const AtlasVaultPlaintextMigrationException();
          }

          final intent = journal
              .withResourceHashes(
                vaultKeySha256: keyDigest,
                storeSha256: storeDigest,
              )
              .withRollbackProgress(rollbackStarted: true);
          journal = await _replaceJournal(journal, intent);
        }

        await _continueRollback(journal);
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  Future<AtlasVaultPlaintextMigrationSummary> _continueRollback(
    AtlasVaultPlaintextMigrationJournal initialJournal,
  ) async {
    var journal = initialJournal;
    if (!journal.rollbackStarted ||
        journal.stage.index >=
            AtlasVaultPlaintextMigrationStage.commitInProgress.index ||
        await _selectedVaultStore.read() != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }

    if (!journal.rollbackStoreDeleted) {
      final store = await _localStoreIO.read(journal.vaultId);
      if (store != null) {
        await _verifyStore(journal: journal, store: store);
        await _localStoreIO.delete(journal.vaultId);
      }
      if (await _localStoreIO.read(journal.vaultId) != null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      journal = await _replaceJournal(
        journal,
        journal.withRollbackProgress(rollbackStoreDeleted: true),
      );
    } else if (await _localStoreIO.read(journal.vaultId) != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }

    final key = await _secureKeyStore.loadVaultKey(journal.vaultId);
    try {
      if (key != null) {
        if (key.length != 32) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        final digest = await vault.atlasVaultSha256Hex(key);
        if (journal.vaultKeySha256 != null &&
            journal.vaultKeySha256 != digest) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        await _secureKeyStore.deleteVaultKey(journal.vaultId);
      }
      if (await _secureKeyStore.containsVaultKey(journal.vaultId)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    } finally {
      _wipe(key);
    }

    if (!await _deleteJournalWithReadBack(journal)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final after = await _readInventory();
    _reviewedInventory = after;
    return after.summary();
  }

  Future<bool> _deleteJournalWithReadBack(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    final journalBytes = journal.canonicalBytes();
    late final String expectedSha256;
    var deleteFailed = false;
    try {
      expectedSha256 = await vault.atlasVaultSha256Hex(journalBytes);
      try {
        await _journalStore.delete(expectedSha256: expectedSha256);
      } catch (_) {
        deleteFailed = true;
      }
    } finally {
      _wipe(journalBytes);
    }
    final restored = await _journalStore.read();
    try {
      if (restored == null) {
        return true;
      }
      if (await vault.atlasVaultSha256Hex(restored) != expectedSha256 ||
          !deleteFailed) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      return false;
    } finally {
      _wipe(restored);
    }
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> finalizeAndActivate() async {
    return _serialized(() async {
      try {
        var journal = await _readJournalRequired();
        if (journal.stage !=
                AtlasVaultPlaintextMigrationStage.encryptedVerified ||
            journal.rollbackStarted) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        await _verifyPreparedResources(journal);
        final current = await _readInventory();
        if (!_journalMatchesInventory(journal, current)) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        journal = await _replaceJournal(
          journal,
          journal.transitionedTo(
            AtlasVaultPlaintextMigrationStage.commitInProgress,
          ),
        );
        _reviewedInventory = null;
        _privateAuthority.hideLegacyPrivateState();
        return await _continueFinalization(journal);
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> resume() async {
    return _serialized(() async {
      try {
        final journal = await _readJournalRequired();
        switch (journal.stage) {
          case AtlasVaultPlaintextMigrationStage.prepared:
            if (journal.rollbackStarted) {
              return await _continueRollback(journal);
            }
            if (await _selectedVaultStore.read() != null) {
              throw const AtlasVaultPlaintextMigrationException();
            }
            return await _continuePreparation(journal);
          case AtlasVaultPlaintextMigrationStage.encryptedVerified:
            if (journal.rollbackStarted) {
              return await _continueRollback(journal);
            }
            await _verifyPreparedResources(journal);
            return _summaryFromJournal(journal);
          case AtlasVaultPlaintextMigrationStage.commitInProgress:
          case AtlasVaultPlaintextMigrationStage.plaintextRemoved:
          case AtlasVaultPlaintextMigrationStage.selectionCommitted:
          case AtlasVaultPlaintextMigrationStage.completionPending:
            _privateAuthority.hideLegacyPrivateState();
            return await _continueFinalization(journal);
        }
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  @override
  Future<AtlasVaultPlaintextMigrationSummary> activateSelected() async {
    return _serialized(() async {
      try {
        final journalBytes = await _journalStore.read();
        try {
          if (journalBytes != null) {
            throw const AtlasVaultPlaintextMigrationException();
          }
        } finally {
          _wipe(journalBytes);
        }
        final selected = await _selectedVaultStore.read();
        if (selected == null) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        if (!_privateAuthority.isEncryptedPrivateStateActive &&
            !await _privateAuthority.activateEncryptedPrivateState(selected)) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        if (!_privateAuthority.isEncryptedPrivateStateActive) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        final snapshot = await _privateAuthority.readEncryptedPrivateState();
        return AtlasVaultPlaintextMigrationSummary(
          savedSearchCount: snapshot.savedSearches.length,
          trackerRecordCount: snapshot.trackerRecords.length,
          localCachePrivatePresent: false,
          compatibilityPrivatePresent: false,
          stage: null,
        );
      } on AtlasVaultPlaintextMigrationException {
        rethrow;
      } catch (_) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    });
  }

  Future<AtlasVaultPlaintextMigrationSummary> _continueFinalization(
    AtlasVaultPlaintextMigrationJournal initialJournal,
  ) async {
    var journal = initialJournal;
    await _verifyPreparedResources(journal);

    if (journal.stage == AtlasVaultPlaintextMigrationStage.commitInProgress) {
      journal = await _removeCompatibilityPrivateState(journal);
      journal = await _removeCachePrivateState(journal);
      _privateAuthority.hideLegacyPrivateState();
      await _verifyPlaintextAbsent(journal);
      journal = await _replaceJournal(
        journal,
        journal.withCommitProgress(
          stage: AtlasVaultPlaintextMigrationStage.plaintextRemoved,
        ),
      );
    }

    if (journal.stage == AtlasVaultPlaintextMigrationStage.plaintextRemoved) {
      final selected = await _selectedVaultStore.read();
      if (selected == null) {
        await _selectedVaultStore.create(journal.vaultId);
      } else if (selected != journal.vaultId) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      if (await _selectedVaultStore.read() != journal.vaultId) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      journal = await _replaceJournal(
        journal,
        journal.withCommitProgress(
          selectionCreated: true,
          stage: AtlasVaultPlaintextMigrationStage.selectionCommitted,
        ),
      );
    }

    if (journal.stage == AtlasVaultPlaintextMigrationStage.selectionCommitted) {
      await _activateAndVerify(journal);
      journal = await _replaceJournal(
        journal,
        journal.transitionedTo(
          AtlasVaultPlaintextMigrationStage.completionPending,
        ),
      );
    }

    if (journal.stage == AtlasVaultPlaintextMigrationStage.completionPending) {
      await _verifyCompletionState(journal);
      if (!await _deleteJournalWithReadBack(journal)) {
        return _summaryFromJournal(journal);
      }
      return _completedSummary(journal);
    }

    throw const AtlasVaultPlaintextMigrationException();
  }

  Future<AtlasVaultPlaintextMigrationJournal> _removeCompatibilityPrivateState(
    AtlasVaultPlaintextMigrationJournal initialJournal,
  ) async {
    _requireJournalCompatibilityAuthority(initialJournal);
    if (initialJournal.cachePrivateSha256 != null) {
      final cache = await _cacheSource.readPrivateStateForMigration();
      final cachePrivateAbsent =
          cache.privateSha256 == null &&
          cache.savedSearches.isEmpty &&
          cache.trackerRecords.isEmpty;
      if (!cache.cachePresent ||
          (!cachePrivateAbsent &&
              cache.privateSha256 != initialJournal.cachePrivateSha256)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      _requireCacheCompatibilityAuthority(
        cache,
        initialJournal.compatibilityAuthority,
        required: true,
      );
    }
    var journal = initialJournal;
    for (final name in journal.remoteSavedSearchNames) {
      var current = await _readBoundCompatibilityPrivateState(journal);
      _validateCompatibilityState(journal, current);
      final present = current.savedSearches.any((value) => value.name == name);
      if (present) {
        _requireJournalCompatibilityAuthority(journal);
        await _compatibilitySource.deleteSavedSearch(name);
        current = await _readBoundCompatibilityPrivateState(journal);
        _validateCompatibilityState(journal, current);
        if (current.savedSearches.any((value) => value.name == name)) {
          throw const AtlasVaultPlaintextMigrationException();
        }
      }
      if (!journal.deletedSavedSearchNames.contains(name)) {
        final completed = <String>[...journal.deletedSavedSearchNames, name]
          ..sort();
        journal = await _replaceJournal(
          journal,
          journal.withCommitProgress(deletedSavedSearchNames: completed),
        );
      }
    }

    for (final handle in journal.remoteTrackerHandles) {
      var current = await _readBoundCompatibilityPrivateState(journal);
      _validateCompatibilityState(journal, current);
      final present = current.trackerRecords.any(
        (value) => value.id == handle.recordId,
      );
      if (present) {
        _requireJournalCompatibilityAuthority(journal);
        await _compatibilitySource.deleteTrackerRecord(handle.recordId);
        current = await _readBoundCompatibilityPrivateState(journal);
        _validateCompatibilityState(journal, current);
        if (current.trackerRecords.any(
          (value) => value.id == handle.recordId,
        )) {
          throw const AtlasVaultPlaintextMigrationException();
        }
      }
      if (!journal.deletedTrackerRecordIds.contains(handle.recordId)) {
        final completed = <String>[
          ...journal.deletedTrackerRecordIds,
          handle.recordId,
        ]..sort();
        journal = await _replaceJournal(
          journal,
          journal.withCommitProgress(deletedTrackerRecordIds: completed),
        );
      }
    }

    final remaining = await _readBoundCompatibilityPrivateState(journal);
    _validateCompatibilityState(journal, remaining);
    if (remaining.savedSearches.isNotEmpty ||
        remaining.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return journal;
  }

  void _validateCompatibilityState(
    AtlasVaultPlaintextMigrationJournal journal,
    AtlasVaultPlaintextPrivateState state,
  ) {
    final expectedSearches = <String, AtlasSavedSearch>{
      for (final value in journal.remoteSavedSearches) value.name: value,
    };
    final expectedSearchNames = journal.remoteSavedSearchNames.toSet();
    for (final value in state.savedSearches) {
      if (!expectedSearchNames.contains(value.name) ||
          journal.deletedSavedSearchNames.contains(value.name)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final expected = expectedSearches[value.name];
      if (expected == null ||
          !_jsonEqual(
            _savedSearchJson(expected),
            _savedSearchJson(_savedSearchFromJournal(_savedSearchJson(value))),
          )) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    }

    final handles = <String, AtlasVaultRemoteTrackerHandle>{
      for (final value in journal.remoteTrackerHandles) value.recordId: value,
    };
    final expectedTrackers = <String, AtlasApplicationRecord>{
      for (final value in journal.remoteTrackerRecords) value.id: value,
    };
    for (final value in state.trackerRecords) {
      final handle = handles[value.id];
      if (handle == null ||
          handle.jobKey != value.jobKey ||
          journal.deletedTrackerRecordIds.contains(value.id)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final expected = expectedTrackers[value.id];
      if (expected == null ||
          !_jsonEqual(
            _trackerJson(expected),
            _trackerJson(_trackerFromJournal(_trackerJson(value))),
          )) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    }
  }

  Future<AtlasVaultPlaintextMigrationJournal> _removeCachePrivateState(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    final current = await _cacheSource.readPrivateStateForMigration();
    final privatePresent =
        current.savedSearches.isNotEmpty || current.trackerRecords.isNotEmpty;
    final expectedDigest = journal.cachePrivateSha256;
    if (journal.cacheCleared) {
      if ((expectedDigest != null && !current.cachePresent) ||
          privatePresent ||
          current.privateSha256 != null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      return journal;
    }
    if (expectedDigest == null) {
      if (privatePresent || current.privateSha256 != null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    } else if (!current.cachePresent) {
      throw const AtlasVaultPlaintextMigrationException();
    } else if (privatePresent) {
      if (current.privateSha256 != expectedDigest) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      await _cacheSource.removePrivateStateForMigration(
        expectedPrivateSha256: expectedDigest,
      );
      final restored = await _cacheSource.readPrivateStateForMigration();
      if (!restored.cachePresent ||
          restored.privateSha256 != null ||
          restored.savedSearches.isNotEmpty ||
          restored.trackerRecords.isNotEmpty) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    } else if (current.privateSha256 != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return _replaceJournal(
      journal,
      journal.withCommitProgress(cacheCleared: true),
    );
  }

  Future<void> _verifyPlaintextAbsent(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    final compatibility = await _readBoundCompatibilityPrivateState(journal);
    if (compatibility.savedSearches.isNotEmpty ||
        compatibility.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final cache = await _cacheSource.readPrivateStateForMigration();
    if ((journal.cachePrivateSha256 != null && !cache.cachePresent) ||
        cache.privateSha256 != null ||
        cache.savedSearches.isNotEmpty ||
        cache.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final memory = await _inMemorySource.readPlaintextPrivateState();
    if (memory.savedSearches.isNotEmpty || memory.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (await _selectedVaultStore.read() != null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    await _verifyPreparedResources(journal);
  }

  Future<void> _activateAndVerify(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    if (await _selectedVaultStore.read() != journal.vaultId) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (!_privateAuthority.isEncryptedPrivateStateActive &&
        !await _privateAuthority.activateEncryptedPrivateState(
          journal.vaultId,
        )) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (!_privateAuthority.isEncryptedPrivateStateActive) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final snapshot = await _privateAuthority.readEncryptedPrivateState();
    if (!await _privateStateMatchesJournal(snapshot, journal)) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  Future<void> _verifyCompletionState(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    if (await _selectedVaultStore.read() != journal.vaultId) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    await _verifyPreparedResources(journal);
    final compatibility = await _readBoundCompatibilityPrivateState(journal);
    if (compatibility.savedSearches.isNotEmpty ||
        compatibility.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final cache = await _cacheSource.readPrivateStateForMigration();
    if ((journal.cachePrivateSha256 != null && !cache.cachePresent) ||
        cache.privateSha256 != null ||
        cache.savedSearches.isNotEmpty ||
        cache.trackerRecords.isNotEmpty) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    await _activateAndVerify(journal);
  }

  Future<bool> _privateStateMatchesJournal(
    AtlasVaultPlaintextPrivateState state,
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    try {
      final savedSearches = <AtlasSavedSearch>[
        for (final value in state.savedSearches)
          _savedSearchFromJournal(_savedSearchJson(value)),
      ]..sort((left, right) => left.name.compareTo(right.name));
      final trackerRecords = <AtlasApplicationRecord>[
        for (final value in state.trackerRecords)
          _trackerFromJournal(_trackerJson(value)),
      ]..sort((left, right) => left.jobKey.compareTo(right.jobKey));
      final digest = await _privateInventoryDigest(
        savedSearches,
        trackerRecords,
      );
      return digest == journal.inventorySha256 &&
          _jsonEqual(
            <Object?>[
              for (final value in savedSearches) _savedSearchJson(value),
            ],
            <Object?>[
              for (final value in journal.savedSearches)
                _savedSearchJson(value),
            ],
          ) &&
          _jsonEqual(
            <Object?>[for (final value in trackerRecords) _trackerJson(value)],
            <Object?>[
              for (final value in journal.trackerRecords) _trackerJson(value),
            ],
          );
    } catch (_) {
      return false;
    }
  }

  Future<T> _serialized<T>(Future<T> Function() operation) async {
    if (_operating) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    _operating = true;
    try {
      return await operation();
    } finally {
      _operating = false;
    }
  }

  Future<AtlasVaultPlaintextMigrationSummary> _continuePreparation(
    AtlasVaultPlaintextMigrationJournal initialJournal,
  ) async {
    var journal = initialJournal;
    Uint8List? key;
    try {
      key = await _secureKeyStore.loadVaultKey(journal.vaultId);
      if (key == null) {
        final supplied = _vaultKeyProvider();
        final generated = Uint8List.fromList(supplied);
        _wipe(supplied);
        try {
          if (generated.length != 32) {
            throw const AtlasVaultPlaintextMigrationException();
          }
          await _secureKeyStore.createVaultKey(journal.vaultId, generated);
        } finally {
          _wipe(generated);
        }
        key = await _secureKeyStore.loadVaultKey(journal.vaultId);
      }
      if (key == null || key.length != 32) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final keyDigest = await vault.atlasVaultSha256Hex(key);
      if (journal.vaultKeySha256 != null &&
          journal.vaultKeySha256 != keyDigest) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      if (journal.vaultKeySha256 == null) {
        journal = await _replaceJournal(
          journal,
          journal.withResourceHashes(vaultKeySha256: keyDigest),
        );
      }

      var store = await _localStoreIO.read(journal.vaultId);
      if (store == null) {
        store = await _buildStore(journal, key);
        await _localStoreIO.create(journal.vaultId, store);
        store = await _localStoreIO.read(journal.vaultId);
      }
      if (store == null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final storeDigest = await _verifyStore(
        journal: journal,
        store: store,
        key: key,
      );
      if (journal.storeSha256 != null && journal.storeSha256 != storeDigest) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      if (journal.stage ==
          AtlasVaultPlaintextMigrationStage.encryptedVerified) {
        return _summaryFromJournal(journal);
      }
      final verified = journal
          .withResourceHashes(storeSha256: storeDigest)
          .transitionedTo(AtlasVaultPlaintextMigrationStage.encryptedVerified);
      journal = await _replaceJournal(journal, verified);
      return _summaryFromJournal(journal);
    } on AtlasVaultPlaintextMigrationException {
      rethrow;
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    } finally {
      _wipe(key);
    }
  }

  Future<void> _verifyPreparedResources(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    Uint8List? key;
    try {
      key = await _secureKeyStore.loadVaultKey(journal.vaultId);
      final store = await _localStoreIO.read(journal.vaultId);
      if (key == null || key.length != 32 || store == null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final keyDigest = await vault.atlasVaultSha256Hex(key);
      if (keyDigest != journal.vaultKeySha256) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      await _verifyStore(journal: journal, store: store, key: key);
    } finally {
      _wipe(key);
    }
  }

  Future<vault.AtlasVaultLocalStore> _buildStore(
    AtlasVaultPlaintextMigrationJournal journal,
    Uint8List key,
  ) async {
    final records = <vault.AtlasVaultEncryptedRecord>[];
    for (final search in journal.savedSearches) {
      records.add(
        await _encryptEnvelope(
          journal: journal,
          key: key,
          envelope: _savedSearchEnvelope(search, journal.createdAt),
        ),
      );
    }
    for (final record in journal.trackerRecords) {
      records.add(
        await _encryptEnvelope(
          journal: journal,
          key: key,
          envelope: _trackerEnvelope(record, journal.createdAt),
        ),
      );
    }
    return vault.AtlasVaultLocalStore.fromJson(<String, Object?>{
      'format': vault.AtlasVaultLocalStore.format,
      'version': vault.AtlasVaultLocalStore.version,
      'store_id': journal.storeId,
      'created_at': journal.createdAt,
      'updated_at': journal.createdAt,
      'vault_metadata': <String, Object?>{
        'format': vault.AtlasVaultMetadata.format,
        'version': vault.AtlasVaultMetadata.version,
        'vault_id': journal.vaultId,
        'crypto': const <String, Object?>{
          'record_aead': 'AES-256-GCM',
          'kdf': 'Argon2id',
          'subkey_kdf': 'HKDF-SHA256',
          'key_wrap_aead': 'AES-256-GCM',
        },
        'key_wraps': const <Object?>[],
      },
      'records': <Object?>[for (final record in records) record.toJson()],
    });
  }

  Future<vault.AtlasVaultEncryptedRecord> _encryptEnvelope({
    required AtlasVaultPlaintextMigrationJournal journal,
    required Uint8List key,
    required vault.AtlasVaultPayloadEnvelope envelope,
  }) async {
    Uint8List? nonce;
    Uint8List? plaintext;
    try {
      final suppliedNonce = _nonceProvider();
      nonce = Uint8List.fromList(suppliedNonce);
      _wipe(suppliedNonce);
      if (nonce.length != vault.AtlasVaultEncryptedRecord.nonceByteCount) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final template =
          vault.AtlasVaultEncryptedRecord.fromJson(<String, Object?>{
            'id': _nextUuid('record.id'),
            'schema_version':
                vault.AtlasVaultEncryptedRecord.supportedSchemaVersion,
            'revision': _nextUuid('record.revision'),
            'parent_revision': null,
            'deleted': false,
            'key_id': _recordKeyId,
            'nonce': base64Encode(nonce),
            'ciphertext': base64Encode(
              Uint8List(vault.AtlasVaultEncryptedRecord.gcmTagByteCount),
            ),
          });
      plaintext = envelope.canonicalBytes();
      return await vault.sealAtlasVaultRecord(
        plaintext: plaintext,
        vaultKey: key,
        vaultId: journal.vaultId,
        record: template,
      );
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    } finally {
      _wipe(nonce);
      _wipe(plaintext);
    }
  }

  Future<String> _verifyStore({
    required AtlasVaultPlaintextMigrationJournal journal,
    required vault.AtlasVaultLocalStore store,
    Uint8List? key,
  }) async {
    Uint8List? ownedKey;
    try {
      if (store.vaultMetadata.vaultId != journal.vaultId ||
          store.storeId != journal.storeId) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final storeBytes = store.canonicalBytes();
      final String digest;
      try {
        digest = await vault.atlasVaultSha256Hex(storeBytes);
      } finally {
        _wipe(storeBytes);
      }
      if (journal.storeSha256 != null && journal.storeSha256 != digest) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      ownedKey = key == null
          ? await _secureKeyStore.loadVaultKey(journal.vaultId)
          : Uint8List.fromList(key);
      if (ownedKey == null || ownedKey.length != 32) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final hydrated = await _hydrateStore(
        vaultId: journal.vaultId,
        key: ownedKey,
        store: store,
        compatibilityAuthority: journal.compatibilityAuthority,
      );
      final expected = _MigrationInventory(
        savedSearches: journal.savedSearches,
        trackerRecords: journal.trackerRecords,
        remoteSavedSearches: journal.remoteSavedSearches,
        remoteTrackerRecords: journal.remoteTrackerRecords,
        remoteSavedSearchNames: journal.remoteSavedSearchNames,
        remoteTrackerHandles: journal.remoteTrackerHandles,
        compatibilityAuthority: journal.compatibilityAuthority,
        cachePrivateSha256: journal.cachePrivateSha256,
        localCachePrivatePresent: journal.cachePrivateSha256 != null,
        compatibilityPrivatePresent:
            journal.remoteSavedSearchNames.isNotEmpty ||
            journal.remoteTrackerHandles.isNotEmpty,
        sha256: journal.inventorySha256,
      );
      if (!_samePrivateInventory(expected, hydrated)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      return digest;
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    } finally {
      _wipe(ownedKey);
    }
  }

  Future<_MigrationInventory> _hydrateStore({
    required String vaultId,
    required Uint8List key,
    required vault.AtlasVaultLocalStore store,
    required String compatibilityAuthority,
  }) async {
    final savedSearches = <AtlasSavedSearch>[];
    final trackerRecords = <AtlasApplicationRecord>[];
    for (final record in store.records) {
      Uint8List? plaintext;
      try {
        if (record.deleted) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        plaintext = await vault.openAtlasVaultRecord(
          vaultKey: key,
          vaultId: vaultId,
          record: record,
        );
        final envelope = vault.AtlasVaultPayloadEnvelope.decodeJson(
          utf8.decode(plaintext, allowMalformed: false),
        );
        switch (envelope.type) {
          case vault.AtlasVaultPayloadType.savedSearch:
            final payload = envelope.payload as vault.AtlasSavedSearchPayload;
            savedSearches.add(
              AtlasSavedSearch(
                name: payload.name,
                description: payload.description,
                request: AtlasSearchRequest.fromJson(payload.request.toJson()),
                createdAt: payload.createdAt,
                updatedAt: payload.updatedAt,
              ),
            );
          case vault.AtlasVaultPayloadType.savedJob:
            final payload = envelope.payload as vault.AtlasSavedJobPayload;
            trackerRecords.add(
              AtlasApplicationRecord(
                id: payload.id ?? '',
                jobKey: payload.jobKey,
                status: payload.status,
                notes: payload.notes,
                appliedAt: payload.appliedAt,
                updatedAt: payload.updatedAt,
              ),
            );
          case vault.AtlasVaultPayloadType.applicationNote:
          case vault.AtlasVaultPayloadType.profileSnippet:
          case vault.AtlasVaultPayloadType.draftMetadata:
            throw const AtlasVaultPlaintextMigrationException();
        }
      } finally {
        _wipe(plaintext);
      }
    }
    savedSearches.sort((left, right) => left.name.compareTo(right.name));
    trackerRecords.sort((left, right) => left.jobKey.compareTo(right.jobKey));
    final digest = await _privateInventoryDigest(savedSearches, trackerRecords);
    return _MigrationInventory(
      savedSearches: savedSearches,
      trackerRecords: trackerRecords,
      remoteSavedSearches: const <AtlasSavedSearch>[],
      remoteTrackerRecords: const <AtlasApplicationRecord>[],
      remoteSavedSearchNames: const <String>[],
      remoteTrackerHandles: const <AtlasVaultRemoteTrackerHandle>[],
      compatibilityAuthority: compatibilityAuthority,
      cachePrivateSha256: null,
      localCachePrivatePresent: false,
      compatibilityPrivatePresent: false,
      sha256: digest,
    );
  }

  Future<_MigrationInventory> _readInventory() async {
    try {
      await _operationAdmission.drainAdmittedPlaintextOperations();
      final memory = await _inMemorySource.readPlaintextPrivateState();
      final cache = await _cacheSource.readPrivateStateForMigration();
      final compatibilityAuthority = _currentCompatibilityAuthority();
      _requireCacheCompatibilityAuthority(cache, compatibilityAuthority);
      final compatibility = await _compatibilitySource
          .readCompatibilityPrivateState();
      if (_currentCompatibilityAuthority() != compatibilityAuthority) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      final savedByName = <String, AtlasSavedSearch>{};
      final trackerByKey = <String, AtlasApplicationRecord>{};
      for (final source in <List<AtlasSavedSearch>>[
        memory.savedSearches,
        cache.savedSearches,
        compatibility.savedSearches,
      ]) {
        for (final value in source) {
          final validated = _savedSearchFromJournal(_savedSearchJson(value));
          final current = savedByName[validated.name];
          savedByName[validated.name] = current == null
              ? validated
              : _mergeSavedSearch(current, validated);
        }
      }
      for (final source in <List<AtlasApplicationRecord>>[
        memory.trackerRecords,
        cache.trackerRecords,
        compatibility.trackerRecords,
      ]) {
        for (final value in source) {
          final validated = _trackerFromJournal(_trackerJson(value));
          final current = trackerByKey[validated.jobKey];
          trackerByKey[validated.jobKey] = current == null
              ? validated
              : _mergeTracker(current, validated);
        }
      }
      final savedSearches = savedByName.values.toList(growable: false)
        ..sort((left, right) => left.name.compareTo(right.name));
      final trackerRecords = trackerByKey.values.toList(growable: false)
        ..sort((left, right) => left.jobKey.compareTo(right.jobKey));
      final remoteSavedSearches = <AtlasSavedSearch>[
        for (final value in compatibility.savedSearches)
          _savedSearchFromJournal(_savedSearchJson(value)),
      ]..sort((left, right) => left.name.compareTo(right.name));
      final remoteSavedSearchNames = remoteSavedSearches
          .map((value) => value.name)
          .toList(growable: false);
      _requireSortedUnique(remoteSavedSearchNames);
      final remoteTrackerRecords =
          <AtlasApplicationRecord>[
            for (final value in compatibility.trackerRecords)
              _trackerFromJournal(_trackerJson(value)),
          ]..sort((left, right) {
            final keyOrder = left.jobKey.compareTo(right.jobKey);
            return keyOrder == 0 ? left.id.compareTo(right.id) : keyOrder;
          });
      final handlesByJobKey = <String, AtlasVaultRemoteTrackerHandle>{};
      for (final value in remoteTrackerRecords) {
        if (value.id.isEmpty || value.jobKey.isEmpty) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        final handle = AtlasVaultRemoteTrackerHandle(
          recordId: value.id,
          jobKey: value.jobKey,
        );
        final current = handlesByJobKey[value.jobKey];
        if (current != null && current.recordId != handle.recordId) {
          throw const AtlasVaultPlaintextMigrationException();
        }
        handlesByJobKey[value.jobKey] = handle;
      }
      final remoteTrackerHandles =
          handlesByJobKey.values.toList(growable: false)..sort((left, right) {
            final keyOrder = left.jobKey.compareTo(right.jobKey);
            return keyOrder == 0
                ? left.recordId.compareTo(right.recordId)
                : keyOrder;
          });
      final digest = await _privateInventoryDigest(
        savedSearches,
        trackerRecords,
      );
      return _MigrationInventory(
        savedSearches: savedSearches,
        trackerRecords: trackerRecords,
        remoteSavedSearchNames: remoteSavedSearchNames,
        remoteTrackerHandles: remoteTrackerHandles,
        remoteSavedSearches: remoteSavedSearches,
        remoteTrackerRecords: remoteTrackerRecords,
        compatibilityAuthority: compatibilityAuthority,
        cachePrivateSha256: cache.privateSha256,
        localCachePrivatePresent:
            cache.savedSearches.isNotEmpty || cache.trackerRecords.isNotEmpty,
        compatibilityPrivatePresent:
            compatibility.savedSearches.isNotEmpty ||
            compatibility.trackerRecords.isNotEmpty,
        sha256: digest,
      );
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  void _requireCacheCompatibilityAuthority(
    AtlasLocalCacheMigrationPrivateState cache,
    String compatibilityAuthority, {
    bool required = false,
  }) {
    if (!required &&
        cache.savedSearches.isEmpty &&
        cache.trackerRecords.isEmpty) {
      return;
    }
    final cacheAuthority = cache.authorityBaseURL;
    if (cacheAuthority == null ||
        cacheAuthority.toString() != compatibilityAuthority) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  String _currentCompatibilityAuthority() {
    try {
      final normalized = AtlasAPIClient.normalizedBaseURL(
        _compatibilitySource.authorityBaseURL.toString(),
      );
      if (normalized == null) {
        throw const AtlasVaultPlaintextMigrationException();
      }
      return normalized.toString();
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  void _requireJournalCompatibilityAuthority(
    AtlasVaultPlaintextMigrationJournal journal,
  ) {
    if (_currentCompatibilityAuthority() != journal.compatibilityAuthority) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  Future<AtlasVaultPlaintextPrivateState> _readBoundCompatibilityPrivateState(
    AtlasVaultPlaintextMigrationJournal journal,
  ) async {
    _requireJournalCompatibilityAuthority(journal);
    final state = await _compatibilitySource.readCompatibilityPrivateState();
    _requireJournalCompatibilityAuthority(journal);
    return state;
  }

  Future<AtlasVaultPlaintextMigrationJournal> _replaceJournal(
    AtlasVaultPlaintextMigrationJournal current,
    AtlasVaultPlaintextMigrationJournal replacement,
  ) async {
    final currentBytes = current.canonicalBytes();
    final replacementBytes = replacement.canonicalBytes();
    try {
      await _journalStore.replace(
        replacementBytes,
        expectedSha256: await vault.atlasVaultSha256Hex(currentBytes),
      );
      final restored = await _readJournalRequired();
      final restoredBytes = restored.canonicalBytes();
      final expectedBytes = replacement.canonicalBytes();
      try {
        if (!_constantTimeEquals(restoredBytes, expectedBytes)) {
          throw const AtlasVaultPlaintextMigrationException();
        }
      } finally {
        _wipe(restoredBytes);
        _wipe(expectedBytes);
      }
      return restored;
    } finally {
      _wipe(currentBytes);
      _wipe(replacementBytes);
    }
  }

  Future<AtlasVaultPlaintextMigrationJournal> _readJournalRequired() async {
    final bytes = await _journalStore.read();
    if (bytes == null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    try {
      return AtlasVaultPlaintextMigrationJournal.decodeBytes(bytes);
    } finally {
      _wipe(bytes);
    }
  }

  AtlasVaultPlaintextMigrationSummary _summaryFromJournal(
    AtlasVaultPlaintextMigrationJournal journal,
  ) {
    return AtlasVaultPlaintextMigrationSummary(
      savedSearchCount: journal.savedSearches.length,
      trackerRecordCount: journal.trackerRecords.length,
      localCachePrivatePresent: journal.cachePrivateSha256 != null,
      compatibilityPrivatePresent:
          journal.remoteSavedSearchNames.isNotEmpty ||
          journal.remoteTrackerHandles.isNotEmpty,
      stage: journal.stage,
    );
  }

  AtlasVaultPlaintextMigrationSummary _completedSummary(
    AtlasVaultPlaintextMigrationJournal journal,
  ) {
    return AtlasVaultPlaintextMigrationSummary(
      savedSearchCount: journal.savedSearches.length,
      trackerRecordCount: journal.trackerRecords.length,
      localCachePrivatePresent: false,
      compatibilityPrivatePresent: false,
      stage: null,
    );
  }

  String _nextUuid(String field) {
    try {
      return requireAtlasVaultCanonicalUuid(_uuidProvider(), field: field);
    } catch (_) {
      throw const AtlasVaultPlaintextMigrationException();
    }
  }

  @override
  String toString() => 'AtlasVaultPlaintextMigrationCoordinator(<redacted>)';
}

final class _MigrationInventory {
  _MigrationInventory({
    required List<AtlasSavedSearch> savedSearches,
    required List<AtlasApplicationRecord> trackerRecords,
    required List<AtlasSavedSearch> remoteSavedSearches,
    required List<AtlasApplicationRecord> remoteTrackerRecords,
    required List<String> remoteSavedSearchNames,
    required List<AtlasVaultRemoteTrackerHandle> remoteTrackerHandles,
    required this.compatibilityAuthority,
    required this.cachePrivateSha256,
    required this.localCachePrivatePresent,
    required this.compatibilityPrivatePresent,
    required this.sha256,
  }) : savedSearches = List<AtlasSavedSearch>.unmodifiable(savedSearches),
       trackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         trackerRecords,
       ),
       remoteSavedSearches = List<AtlasSavedSearch>.unmodifiable(
         remoteSavedSearches,
       ),
       remoteTrackerRecords = List<AtlasApplicationRecord>.unmodifiable(
         remoteTrackerRecords,
       ),
       remoteSavedSearchNames = List<String>.unmodifiable(
         remoteSavedSearchNames,
       ),
       remoteTrackerHandles = List<AtlasVaultRemoteTrackerHandle>.unmodifiable(
         remoteTrackerHandles,
       ) {
    _requiredCompatibilityAuthority(compatibilityAuthority);
  }

  final List<AtlasSavedSearch> savedSearches;
  final List<AtlasApplicationRecord> trackerRecords;
  final List<AtlasSavedSearch> remoteSavedSearches;
  final List<AtlasApplicationRecord> remoteTrackerRecords;
  final List<String> remoteSavedSearchNames;
  final List<AtlasVaultRemoteTrackerHandle> remoteTrackerHandles;
  final String compatibilityAuthority;
  final String? cachePrivateSha256;
  final bool localCachePrivatePresent;
  final bool compatibilityPrivatePresent;
  final String sha256;

  AtlasVaultPlaintextMigrationSummary summary({
    AtlasVaultPlaintextMigrationStage? stage,
  }) {
    return AtlasVaultPlaintextMigrationSummary(
      savedSearchCount: savedSearches.length,
      trackerRecordCount: trackerRecords.length,
      localCachePrivatePresent: localCachePrivatePresent,
      compatibilityPrivatePresent: compatibilityPrivatePresent,
      stage: stage,
    );
  }
}

vault.AtlasVaultPayloadEnvelope _savedSearchEnvelope(
  AtlasSavedSearch value,
  String timestamp,
) {
  final text = value.request.text?.trim();
  return vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
    'type': vault.AtlasVaultPayloadType.savedSearch.wireName,
    'payload_schema': vault.AtlasVaultPayloadEnvelope.supportedPayloadSchema,
    'payload': <String, Object?>{
      'name': value.name,
      'summary': text == null || text.isEmpty ? 'All open jobs' : text,
      if (value.description != null) 'description': value.description,
      'request': vault.AtlasSearchRequest.fromJson(
        value.request.toJson(),
      ).toJson(),
      if (value.createdAt != null) 'created_at': value.createdAt,
      if (value.updatedAt != null) 'updated_at': value.updatedAt,
    },
    'client_created_at': timestamp,
    'client_updated_at': timestamp,
  });
}

vault.AtlasVaultPayloadEnvelope _trackerEnvelope(
  AtlasApplicationRecord value,
  String timestamp,
) {
  return vault.AtlasVaultPayloadEnvelope.fromJson(<String, Object?>{
    'type': vault.AtlasVaultPayloadType.savedJob.wireName,
    'payload_schema': vault.AtlasVaultPayloadEnvelope.supportedPayloadSchema,
    'payload': <String, Object?>{
      if (value.id.isNotEmpty) 'id': value.id,
      'job_key': value.jobKey,
      'status': value.status,
      if (value.notes != null) 'notes': value.notes,
      if (value.appliedAt != null) 'applied_at': value.appliedAt,
      if (value.updatedAt != null) 'updated_at': value.updatedAt,
    },
    'client_created_at': timestamp,
    'client_updated_at': timestamp,
  });
}

AtlasSavedSearch _mergeSavedSearch(
  AtlasSavedSearch left,
  AtlasSavedSearch right,
) {
  if (left.name != right.name ||
      !_jsonEqual(left.request.toJson(), right.request.toJson())) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  return AtlasSavedSearch(
    name: left.name,
    description: _mergeOptional(left.description, right.description),
    request: AtlasSearchRequest.fromJson(left.request.toJson()),
    createdAt: _mergeOptional(left.createdAt, right.createdAt),
    updatedAt: _mergeOptional(left.updatedAt, right.updatedAt),
  );
}

AtlasApplicationRecord _mergeTracker(
  AtlasApplicationRecord left,
  AtlasApplicationRecord right,
) {
  if (left.jobKey != right.jobKey || left.status != right.status) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  return AtlasApplicationRecord(
    id: _mergeOptional(left.id, right.id) ?? '',
    jobKey: left.jobKey,
    status: left.status,
    notes: _mergeOptional(left.notes, right.notes),
    appliedAt: _mergeOptional(left.appliedAt, right.appliedAt),
    updatedAt: _mergeOptional(left.updatedAt, right.updatedAt),
  );
}

String? _mergeOptional(String? left, String? right) {
  final leftValue = left == null || left.isEmpty ? null : left;
  final rightValue = right == null || right.isEmpty ? null : right;
  if (leftValue != null && rightValue != null && leftValue != rightValue) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  return leftValue ?? rightValue ?? (left != null || right != null ? '' : null);
}

AtlasSavedSearch _savedSearchFromJournal(Map<String, Object?> value) {
  _requireExactKeys(value, const <String>{
    'name',
    'description',
    'request',
    'created_at',
    'updated_at',
  });
  final request = vault.AtlasSearchRequest.fromJson(
    _stringMap(value['request']),
  );
  return AtlasSavedSearch(
    name: _requiredText(value['name']),
    description: _optionalText(value['description']),
    request: AtlasSearchRequest.fromJson(request.toJson()),
    createdAt: _optionalUtc(value['created_at']),
    updatedAt: _optionalUtc(value['updated_at']),
  );
}

AtlasApplicationRecord _trackerFromJournal(Map<String, Object?> value) {
  _requireExactKeys(value, const <String>{
    'id',
    'job_key',
    'status',
    'notes',
    'applied_at',
    'updated_at',
  });
  return AtlasApplicationRecord(
    id: _requiredText(value['id'], allowEmpty: true),
    jobKey: _requiredText(value['job_key']),
    status: _requiredText(value['status']),
    notes: _optionalText(value['notes']),
    appliedAt: _optionalUtc(value['applied_at']),
    updatedAt: _optionalUtc(value['updated_at']),
  );
}

Map<String, Object?> _savedSearchJson(AtlasSavedSearch value) {
  return <String, Object?>{
    'name': value.name,
    'description': value.description,
    'request': vault.AtlasSearchRequest.fromJson(
      value.request.toJson(),
    ).toJson(),
    'created_at': _normalizeLegacyUtc(value.createdAt),
    'updated_at': _normalizeLegacyUtc(value.updatedAt),
  };
}

Map<String, Object?> _trackerJson(AtlasApplicationRecord value) {
  return <String, Object?>{
    'id': value.id,
    'job_key': value.jobKey,
    'status': value.status,
    'notes': value.notes,
    'applied_at': _normalizeLegacyUtc(value.appliedAt),
    'updated_at': _normalizeLegacyUtc(value.updatedAt),
  };
}

Future<String> _privateInventoryDigest(
  List<AtlasSavedSearch> savedSearches,
  List<AtlasApplicationRecord> trackerRecords,
) async {
  final bytes = encodeCanonicalJson(<String, Object?>{
    'saved_searches': <Object?>[
      for (final value in savedSearches) _savedSearchJson(value),
    ],
    'tracker_records': <Object?>[
      for (final value in trackerRecords) _trackerJson(value),
    ],
  });
  try {
    return await vault.atlasVaultSha256Hex(bytes);
  } finally {
    _wipe(bytes);
  }
}

bool _journalMatchesInventory(
  AtlasVaultPlaintextMigrationJournal journal,
  _MigrationInventory inventory,
) {
  return journal.inventorySha256 == inventory.sha256 &&
      _samePrivateInventory(
        _MigrationInventory(
          savedSearches: journal.savedSearches,
          trackerRecords: journal.trackerRecords,
          remoteSavedSearches: journal.remoteSavedSearches,
          remoteTrackerRecords: journal.remoteTrackerRecords,
          remoteSavedSearchNames: journal.remoteSavedSearchNames,
          remoteTrackerHandles: journal.remoteTrackerHandles,
          compatibilityAuthority: journal.compatibilityAuthority,
          cachePrivateSha256: journal.cachePrivateSha256,
          localCachePrivatePresent: journal.cachePrivateSha256 != null,
          compatibilityPrivatePresent:
              journal.remoteSavedSearchNames.isNotEmpty ||
              journal.remoteTrackerHandles.isNotEmpty,
          sha256: journal.inventorySha256,
        ),
        inventory,
      ) &&
      journal.compatibilityAuthority == inventory.compatibilityAuthority &&
      journal.cachePrivateSha256 == inventory.cachePrivateSha256 &&
      _jsonEqual(
        journal.remoteSavedSearchNames,
        inventory.remoteSavedSearchNames,
      ) &&
      _jsonEqual(
        <Object?>[
          for (final value in journal.remoteSavedSearches)
            _savedSearchJson(value),
        ],
        <Object?>[
          for (final value in inventory.remoteSavedSearches)
            _savedSearchJson(value),
        ],
      ) &&
      _jsonEqual(
        <Object?>[
          for (final value in journal.remoteTrackerRecords) _trackerJson(value),
        ],
        <Object?>[
          for (final value in inventory.remoteTrackerRecords)
            _trackerJson(value),
        ],
      ) &&
      _jsonEqual(
        <Object?>[
          for (final value in journal.remoteTrackerHandles) value.toJson(),
        ],
        <Object?>[
          for (final value in inventory.remoteTrackerHandles) value.toJson(),
        ],
      );
}

bool _samePrivateInventory(
  _MigrationInventory left,
  _MigrationInventory right,
) {
  return left.sha256 == right.sha256 &&
      _jsonEqual(
        <String, Object?>{
          'saved_searches': <Object?>[
            for (final value in left.savedSearches) _savedSearchJson(value),
          ],
          'tracker_records': <Object?>[
            for (final value in left.trackerRecords) _trackerJson(value),
          ],
        },
        <String, Object?>{
          'saved_searches': <Object?>[
            for (final value in right.savedSearches) _savedSearchJson(value),
          ],
          'tracker_records': <Object?>[
            for (final value in right.trackerRecords) _trackerJson(value),
          ],
        },
      );
}

bool _sameInventory(_MigrationInventory left, _MigrationInventory right) {
  return _samePrivateInventory(left, right) &&
      left.compatibilityAuthority == right.compatibilityAuthority &&
      left.cachePrivateSha256 == right.cachePrivateSha256 &&
      left.localCachePrivatePresent == right.localCachePrivatePresent &&
      left.compatibilityPrivatePresent == right.compatibilityPrivatePresent &&
      _jsonEqual(left.remoteSavedSearchNames, right.remoteSavedSearchNames) &&
      _jsonEqual(
        <Object?>[
          for (final value in left.remoteSavedSearches) _savedSearchJson(value),
        ],
        <Object?>[
          for (final value in right.remoteSavedSearches)
            _savedSearchJson(value),
        ],
      ) &&
      _jsonEqual(
        <Object?>[
          for (final value in left.remoteTrackerRecords) _trackerJson(value),
        ],
        <Object?>[
          for (final value in right.remoteTrackerRecords) _trackerJson(value),
        ],
      ) &&
      _jsonEqual(
        <Object?>[
          for (final value in left.remoteTrackerHandles) value.toJson(),
        ],
        <Object?>[
          for (final value in right.remoteTrackerHandles) value.toJson(),
        ],
      );
}

bool _jsonEqual(Object? left, Object? right) {
  final leftBytes = encodeCanonicalJson(left);
  final rightBytes = encodeCanonicalJson(right);
  try {
    return _constantTimeEquals(leftBytes, rightBytes);
  } finally {
    _wipe(leftBytes);
    _wipe(rightBytes);
  }
}

bool _constantTimeEquals(Uint8List left, Uint8List right) {
  var difference = left.length ^ right.length;
  final maximum = max(left.length, right.length);
  for (var index = 0; index < maximum; index += 1) {
    difference |=
        (index < left.length ? left[index] : 0) ^
        (index < right.length ? right[index] : 0);
  }
  return difference == 0;
}

Map<String, Object?> _stringMap(Object? value) {
  if (value is! Map) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  final output = <String, Object?>{};
  for (final entry in value.entries) {
    if (entry.key is! String) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    output[entry.key as String] = entry.value;
  }
  return output;
}

List<Object?> _requiredList(Object? value) {
  if (value is! List) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  return List<Object?>.unmodifiable(value.cast<Object?>());
}

void _requireExactKeys(Map<String, Object?> value, Set<String> expected) {
  if (value.keys.length != expected.length ||
      !value.keys.every(expected.contains)) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String _requiredText(Object? value, {bool allowEmpty = false}) {
  try {
    return requireAtlasVaultString(
      value,
      field: 'migration.value',
      allowEmpty: allowEmpty,
    );
  } catch (_) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String _requiredCompatibilityAuthority(Object? value) {
  try {
    final text = _requiredText(value);
    final normalized = AtlasAPIClient.normalizedBaseURL(text);
    if (normalized == null || normalized.toString() != text) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    return text;
  } catch (_) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String? _optionalText(Object? value) {
  if (value == null) {
    return null;
  }
  return _requiredText(value, allowEmpty: true);
}

String? _optionalUtc(Object? value) {
  if (value == null) {
    return null;
  }
  try {
    return requireAtlasVaultUtcSeconds(value, field: 'migration.timestamp');
  } catch (_) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String? _normalizeLegacyUtc(String? value) {
  if (value == null) {
    return null;
  }
  try {
    final match = RegExp(
      r'^(\d{4})-(\d{2})-(\d{2})T'
      r'(\d{2}):(\d{2}):(\d{2})'
      r'(?:\.(\d{1,6}))?'
      r'(Z|([+-])(\d{2}):(\d{2}))$',
    ).firstMatch(value);
    if (match == null) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final year = int.parse(match.group(1)!);
    final month = int.parse(match.group(2)!);
    final day = int.parse(match.group(3)!);
    final hour = int.parse(match.group(4)!);
    final minute = int.parse(match.group(5)!);
    final second = int.parse(match.group(6)!);
    final fraction = (match.group(7) ?? '').padRight(6, '0');
    final microseconds = fraction.isEmpty ? 0 : int.parse(fraction);
    final wallClock = DateTime.utc(
      year,
      month,
      day,
      hour,
      minute,
      second,
      microseconds ~/ Duration.microsecondsPerMillisecond,
      microseconds % Duration.microsecondsPerMillisecond,
    );
    if (year == 0 ||
        wallClock.year != year ||
        wallClock.month != month ||
        wallClock.day != day ||
        wallClock.hour != hour ||
        wallClock.minute != minute ||
        wallClock.second != second ||
        wallClock.millisecond * Duration.microsecondsPerMillisecond +
                wallClock.microsecond !=
            microseconds) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    if (match.group(8) != 'Z') {
      final offsetHour = int.parse(match.group(10)!);
      final offsetMinute = int.parse(match.group(11)!);
      if (offsetHour > 14 ||
          offsetMinute > 59 ||
          (offsetHour == 14 && offsetMinute != 0)) {
        throw const AtlasVaultPlaintextMigrationException();
      }
    }
    final parsed = DateTime.tryParse(value);
    if (parsed == null || !parsed.isUtc) {
      throw const AtlasVaultPlaintextMigrationException();
    }
    final normalized = _utcSeconds(parsed);
    requireAtlasVaultUtcSeconds(normalized, field: 'migration.timestamp');
    return normalized;
  } catch (_) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String _requiredSha256(Object? value) {
  if (value is! String || !RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw const AtlasVaultPlaintextMigrationException();
  }
  return value;
}

String? _optionalSha256(Object? value) {
  if (value == null) {
    return null;
  }
  return _requiredSha256(value);
}

void _requireSortedUnique(List<String> values) {
  final sorted = values.toList(growable: false)..sort();
  if (values.length != values.toSet().length || !_jsonEqual(values, sorted)) {
    throw const AtlasVaultPlaintextMigrationException();
  }
}

String _utcSeconds(DateTime value) {
  final utc = value.toUtc();
  String two(int number) => number.toString().padLeft(2, '0');
  return '${utc.year.toString().padLeft(4, '0')}-'
      '${two(utc.month)}-${two(utc.day)}T'
      '${two(utc.hour)}:${two(utc.minute)}:${two(utc.second)}Z';
}

String _secureUuidV4() {
  final bytes = _secureBytes(16);
  try {
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
        '${hex.substring(20)}';
  } finally {
    _wipe(bytes);
  }
}

Uint8List _secureVaultKey() => _secureBytes(32);

Uint8List _secureNonce() =>
    _secureBytes(vault.AtlasVaultEncryptedRecord.nonceByteCount);

Uint8List _secureBytes(int count) {
  final random = Random.secure();
  return Uint8List.fromList(
    List<int>.generate(count, (_) => random.nextInt(256)),
  );
}

void _wipe(Uint8List? value) {
  value?.fillRange(0, value.length, 0);
}
