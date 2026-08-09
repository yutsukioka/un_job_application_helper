import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'canonical_json.dart';
import 'crypto.dart';
import 'export.dart';
import 'local_store_io.dart';
import 'models.dart';
import 'payloads.dart';
import 'plaintext_migration.dart';
import 'private_state_runtime.dart';
import 'recovery.dart';
import 'strict_values.dart';

final class AtlasVaultInteroperabilityException implements Exception {
  const AtlasVaultInteroperabilityException();

  @override
  String toString() => 'AtlasVault interoperability operation failed.';
}

abstract interface class AtlasVaultEncryptedDocumentTransport {
  Future<Uint8List?> pickEncryptedExport();

  Future<bool> saveEncryptedExport(Uint8List canonicalExportBytes);
}

abstract interface class AtlasVaultRecoveryDisplayHandle {
  String? take();

  void destroy();
}

enum AtlasVaultRecoveryExportDisposition {
  exportReady,
  saved,
  cancelled,
  failed,
  recoveryRequired,
  unavailable,
}

final class AtlasVaultRecoveryExportAvailability {
  const AtlasVaultRecoveryExportAvailability({
    required this.available,
    required this.encryptedRecordCount,
    required this.recoveryWrapPresent,
  });

  final bool available;
  final int encryptedRecordCount;
  final bool recoveryWrapPresent;

  @override
  String toString() => 'AtlasVaultRecoveryExportAvailability(<redacted>)';
}

final class AtlasVaultRecoveryExportResult {
  const AtlasVaultRecoveryExportResult({
    required this.disposition,
    required this.encryptedRecordCount,
    required this.recoveryWrapPresent,
  });

  final AtlasVaultRecoveryExportDisposition disposition;
  final int encryptedRecordCount;
  final bool recoveryWrapPresent;

  @override
  String toString() => 'AtlasVaultRecoveryExportResult(<redacted>)';
}

enum AtlasVaultRecoveryImportDisposition {
  ready,
  importPrepared,
  importedAndActive,
  cancelled,
  migrationRequired,
  existingVault,
  resumeRequired,
  completionPending,
  failed,
  recoveryRequired,
  unavailable,
}

final class AtlasVaultRecoveryImportResult {
  const AtlasVaultRecoveryImportResult({
    required this.disposition,
    required this.encryptedRecordCount,
    required this.pendingImport,
  });

  final AtlasVaultRecoveryImportDisposition disposition;
  final int encryptedRecordCount;
  final bool pendingImport;

  @override
  String toString() => 'AtlasVaultRecoveryImportResult(<redacted>)';
}

enum AtlasVaultRecoveryImportStage {
  prepared('prepared'),
  storeCreated('store_created'),
  keyCreated('key_created'),
  selectionCommitted('selection_committed'),
  completionPending('completion_pending');

  const AtlasVaultRecoveryImportStage(this.wireName);

  final String wireName;

  static AtlasVaultRecoveryImportStage parse(Object? value) {
    if (value is String) {
      for (final stage in values) {
        if (stage.wireName == value) {
          return stage;
        }
      }
    }
    throw const AtlasVaultInteroperabilityException();
  }
}

enum AtlasVaultRecoveryImportProfile {
  android('atlasvault-android-recovery-import'),
  windows('atlasvault-windows-recovery-import');

  const AtlasVaultRecoveryImportProfile(this.format);

  final String format;
}

abstract interface class AtlasVaultProtectedRecoveryImportJournalStore {
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

final class AtlasVaultRecoveryImportJournal {
  AtlasVaultRecoveryImportJournal._({
    required this.profile,
    required this.importId,
    required this.stage,
    required this.exportId,
    required this.vaultId,
    required this.storeId,
    required this.createdAt,
    required this.exportSha256,
    required this.localStoreSha256,
    required this.vaultKeySha256,
  });

  static const format = 'atlasvault-android-recovery-import';
  static const version = 1;

  final AtlasVaultRecoveryImportProfile profile;
  final String importId;
  final AtlasVaultRecoveryImportStage stage;
  final String exportId;
  final String vaultId;
  final String storeId;
  final String createdAt;
  final String exportSha256;
  final String localStoreSha256;
  final String vaultKeySha256;

  factory AtlasVaultRecoveryImportJournal.prepared({
    AtlasVaultRecoveryImportProfile profile =
        AtlasVaultRecoveryImportProfile.android,
    required String importId,
    required String exportId,
    required String vaultId,
    required String storeId,
    required String createdAt,
    required String exportSha256,
    required String localStoreSha256,
    required String vaultKeySha256,
  }) {
    return AtlasVaultRecoveryImportJournal.fromJson(<String, Object?>{
      'format': profile.format,
      'version': version,
      'import_id': importId,
      'stage': AtlasVaultRecoveryImportStage.prepared.wireName,
      'export_id': exportId,
      'vault_id': vaultId,
      'store_id': storeId,
      'created_at': createdAt,
      'export_sha256': exportSha256,
      'local_store_sha256': localStoreSha256,
      'vault_key_sha256': vaultKeySha256,
    }, profile: profile);
  }

  factory AtlasVaultRecoveryImportJournal.decodeBytes(
    Uint8List source, {
    AtlasVaultRecoveryImportProfile profile =
        AtlasVaultRecoveryImportProfile.android,
  }) {
    try {
      return AtlasVaultRecoveryImportJournal.fromJson(
        decodeAtlasVaultJsonObject(
          utf8.decode(source, allowMalformed: false),
          context: 'Recovery import journal',
        ),
        profile: profile,
      );
    } catch (_) {
      throw const AtlasVaultInteroperabilityException();
    }
  }

  factory AtlasVaultRecoveryImportJournal.fromJson(
    Map<String, Object?> source, {
    AtlasVaultRecoveryImportProfile profile =
        AtlasVaultRecoveryImportProfile.android,
  }) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'import_id',
          'stage',
          'export_id',
          'vault_id',
          'store_id',
          'created_at',
          'export_sha256',
          'local_store_sha256',
          'vault_key_sha256',
        },
        context: 'Recovery import journal',
      );
      if (value['format'] != profile.format ||
          requireAtlasVaultInt(
                value['version'],
                field: 'recovery_import.version',
              ) !=
              version) {
        throw const AtlasVaultInteroperabilityException();
      }
      return AtlasVaultRecoveryImportJournal._(
        profile: profile,
        importId: requireAtlasVaultCanonicalUuid(
          value['import_id'],
          field: 'recovery_import.import_id',
        ),
        stage: AtlasVaultRecoveryImportStage.parse(value['stage']),
        exportId: requireAtlasVaultCanonicalUuid(
          value['export_id'],
          field: 'recovery_import.export_id',
        ),
        vaultId: requireAtlasVaultVaultId(value['vault_id']),
        storeId: requireAtlasVaultCanonicalUuid(
          value['store_id'],
          field: 'recovery_import.store_id',
        ),
        createdAt: requireAtlasVaultUtcSeconds(
          value['created_at'],
          field: 'recovery_import.created_at',
        ),
        exportSha256: _requireSha256(value['export_sha256']),
        localStoreSha256: _requireSha256(value['local_store_sha256']),
        vaultKeySha256: _requireSha256(value['vault_key_sha256']),
      );
    } catch (_) {
      throw const AtlasVaultInteroperabilityException();
    }
  }

  AtlasVaultRecoveryImportJournal transitionedTo(
    AtlasVaultRecoveryImportStage next,
  ) {
    if (next.index != stage.index + 1) {
      throw const AtlasVaultInteroperabilityException();
    }
    return AtlasVaultRecoveryImportJournal.fromJson(<String, Object?>{
      ...toJson(),
      'stage': next.wireName,
    }, profile: profile);
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': profile.format,
    'version': version,
    'import_id': importId,
    'stage': stage.wireName,
    'export_id': exportId,
    'vault_id': vaultId,
    'store_id': storeId,
    'created_at': createdAt,
    'export_sha256': exportSha256,
    'local_store_sha256': localStoreSha256,
    'vault_key_sha256': vaultKeySha256,
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  static String _requireSha256(Object? value) {
    final text = requireAtlasVaultString(
      value,
      field: 'recovery_import.sha256',
      allowEmpty: false,
    );
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(text)) {
      throw const AtlasVaultInteroperabilityException();
    }
    return text;
  }

  @override
  String toString() => 'AtlasVaultRecoveryImportJournal(<redacted>)';
}

abstract interface class AtlasVaultInteroperabilityCoordinating {
  Future<AtlasVaultRecoveryExportAvailability> inspectRecoveryExport();

  Future<AtlasVaultRecoveryDisplayHandle> beginRecoverySetup();

  Future<AtlasVaultRecoveryExportResult> confirmRecoverySetup(
    String recoveryKeyText,
  );

  Future<AtlasVaultRecoveryExportResult> prepareExistingRecoveryExport(
    String recoveryKeyText,
  );

  Future<AtlasVaultRecoveryExportResult> savePreparedExport();

  Future<AtlasVaultRecoveryImportResult> inspectRecoveryImport();

  Future<AtlasVaultRecoveryImportResult> prepareRecoveryImport();

  Future<AtlasVaultRecoveryImportResult> confirmRecoveryImport(
    String recoveryKeyText,
  );

  Future<AtlasVaultRecoveryImportResult> discardPendingImport();

  void discardPendingRecovery();

  Future<void> stop();
}

abstract interface class AtlasVaultRecoveryImportOperationAdmission {
  Future<void> beginRecoveryImportAdmission();

  void endRecoveryImportAdmission();
}

abstract interface class AtlasVaultRecoveryImportTransactionAdmission {
  Future<T> runRecoveryImportTransaction<T>(Future<T> Function() operation);
}

final class _NoopRecoveryImportTransactionAdmission
    implements AtlasVaultRecoveryImportTransactionAdmission {
  const _NoopRecoveryImportTransactionAdmission();

  @override
  Future<T> runRecoveryImportTransaction<T>(Future<T> Function() operation) =>
      operation();
}

final class _NoopRecoveryImportOperationAdmission
    implements AtlasVaultRecoveryImportOperationAdmission {
  const _NoopRecoveryImportOperationAdmission();

  @override
  Future<void> beginRecoveryImportAdmission() async {}

  @override
  void endRecoveryImportAdmission() {}
}

final class AtlasVaultInteroperabilityCoordinator
    implements AtlasVaultInteroperabilityCoordinating {
  static const int _maximumDocumentByteCount = 128 * 1024 * 1024;

  AtlasVaultInteroperabilityCoordinator({
    required AtlasVaultPrivateStateRuntime runtime,
    required AtlasVaultSelectedVaultStore selectedVaultStore,
    required AtlasVaultProtectedMigrationJournalStore migrationJournalStore,
    required Future<bool> Function() recoveryImportPending,
    required AtlasVaultEncryptedDocumentTransport documentTransport,
    AtlasVaultProtectedRecoveryImportJournalStore? recoveryImportJournalStore,
    AtlasVaultMigrationSecureKeyStore? secureKeyStore,
    AtlasVaultLocalStoreIO? localStoreIO,
    AtlasVaultPlaintextStateSource? inMemorySource,
    AtlasVaultCompatibilityPrivateSource? compatibilitySource,
    AtlasLocalCacheMigrationSource? cacheSource,
    AtlasVaultRecoveryImportOperationAdmission? importOperationAdmission,
    AtlasVaultRecoveryImportTransactionAdmission? importTransactionAdmission,
    AtlasVaultRecoveryImportProfile recoveryImportProfile =
        AtlasVaultRecoveryImportProfile.android,
    Future<bool> Function(String vaultId)? activateImportedVault,
    void Function(bool pending)? recoveryImportPendingDidChange,
    DateTime Function()? now,
    String Function()? uuidProvider,
    String Function()? importIdProvider,
    String Function()? importStoreIdProvider,
    AtlasVaultRecoveryKey Function()? recoveryKeyProvider,
    Uint8List Function()? recoverySaltProvider,
    Uint8List Function()? recoveryNonceProvider,
  }) : // Keep dependency labels explicit at construction sites.
       // ignore: prefer_initializing_formals
       _runtime = runtime,
       // ignore: prefer_initializing_formals
       _selectedVaultStore = selectedVaultStore,
       // ignore: prefer_initializing_formals
       _migrationJournalStore = migrationJournalStore,
       // ignore: prefer_initializing_formals
       _recoveryImportPending = recoveryImportPending,
       // ignore: prefer_initializing_formals
       _documentTransport = documentTransport,
       // Keep optional import dependencies explicit at construction sites.
       // ignore: prefer_initializing_formals
       _recoveryImportJournalStore = recoveryImportJournalStore,
       // ignore: prefer_initializing_formals
       _secureKeyStore = secureKeyStore,
       // ignore: prefer_initializing_formals
       _localStoreIO = localStoreIO,
       // ignore: prefer_initializing_formals
       _inMemorySource = inMemorySource,
       // ignore: prefer_initializing_formals
       _compatibilitySource = compatibilitySource,
       // ignore: prefer_initializing_formals
       _cacheSource = cacheSource,
       _importOperationAdmission =
           importOperationAdmission ??
           const _NoopRecoveryImportOperationAdmission(),
       _importTransactionAdmission =
           importTransactionAdmission ??
           const _NoopRecoveryImportTransactionAdmission(),
       // ignore: prefer_initializing_formals
       _recoveryImportProfile = recoveryImportProfile,
       _activateImportedVault =
           activateImportedVault ??
           ((vaultId) async =>
               await runtime.activateExisting(vaultId) ==
               AtlasVaultActivationResult.activated),
       _recoveryImportPendingDidChange =
           recoveryImportPendingDidChange ?? _ignoreImportPendingChange,
       _now = now ?? DateTime.now,
       _uuidProvider = uuidProvider ?? _secureUuidV4,
       _importIdProvider = importIdProvider ?? _secureUuidV4,
       _importStoreIdProvider = importStoreIdProvider ?? _secureUuidV4,
       _recoveryKeyProvider =
           recoveryKeyProvider ?? AtlasVaultRecoveryKey.generate,
       _recoverySaltProvider =
           recoverySaltProvider ??
           (() =>
               _secureBytes(AtlasVaultRecoveryWrapKdfParameters.saltByteCount)),
       _recoveryNonceProvider =
           recoveryNonceProvider ??
           (() => _secureBytes(AtlasVaultRecoveryKeyWrapV2.nonceByteCount));

  final AtlasVaultPrivateStateRuntime _runtime;
  final AtlasVaultSelectedVaultStore _selectedVaultStore;
  final AtlasVaultProtectedMigrationJournalStore _migrationJournalStore;
  final Future<bool> Function() _recoveryImportPending;
  final AtlasVaultEncryptedDocumentTransport _documentTransport;
  final AtlasVaultProtectedRecoveryImportJournalStore?
  _recoveryImportJournalStore;
  final AtlasVaultMigrationSecureKeyStore? _secureKeyStore;
  final AtlasVaultLocalStoreIO? _localStoreIO;
  final AtlasVaultPlaintextStateSource? _inMemorySource;
  final AtlasVaultCompatibilityPrivateSource? _compatibilitySource;
  final AtlasLocalCacheMigrationSource? _cacheSource;
  final AtlasVaultRecoveryImportOperationAdmission _importOperationAdmission;
  final AtlasVaultRecoveryImportTransactionAdmission
  _importTransactionAdmission;
  final AtlasVaultRecoveryImportProfile _recoveryImportProfile;
  final Future<bool> Function(String vaultId) _activateImportedVault;
  final void Function(bool pending) _recoveryImportPendingDidChange;
  final DateTime Function() _now;
  final String Function() _uuidProvider;
  final String Function() _importIdProvider;
  final String Function() _importStoreIdProvider;
  final AtlasVaultRecoveryKey Function() _recoveryKeyProvider;
  final Uint8List Function() _recoverySaltProvider;
  final Uint8List Function() _recoveryNonceProvider;

  AtlasVaultRecoveryKey? _pendingRecoveryKey;
  Uint8List? _preparedExportBytes;
  int _preparedRecordCount = 0;
  AtlasVaultEncryptedExport? _preparedImport;
  Uint8List? _preparedImportBytes;
  String? _preparedImportSha256;
  Future<void>? _operation;
  bool _stopped = false;
  bool _discardWhenIdle = false;
  int _generation = 0;

  @override
  Future<AtlasVaultRecoveryExportAvailability> inspectRecoveryExport() {
    return _retain(() async {
      try {
        return await _withAuthorizedSession((session) async {
          final wraps = _recoveryWraps(session.localStore.vaultMetadata);
          return AtlasVaultRecoveryExportAvailability(
            available: wraps.length <= 1,
            encryptedRecordCount: session.localStore.records.length,
            recoveryWrapPresent: wraps.length == 1,
          );
        });
      } catch (_) {
        return const AtlasVaultRecoveryExportAvailability(
          available: false,
          encryptedRecordCount: 0,
          recoveryWrapPresent: false,
        );
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryDisplayHandle> beginRecoverySetup() {
    return _retain(() async {
      _clearPendingRecovery();
      _clearPreparedExport();
      final available = await _withAuthorizedSession((session) async {
        return _recoveryWraps(session.localStore.vaultMetadata).isEmpty;
      });
      if (!available) {
        throw const AtlasVaultInteroperabilityException();
      }
      final key = _recoveryKeyProvider();
      String text;
      try {
        text = key.canonicalText;
      } catch (_) {
        key.destroy();
        throw const AtlasVaultInteroperabilityException();
      }
      _pendingRecoveryKey = key;
      return _OneShotRecoveryDisplayHandle(text);
    });
  }

  @override
  Future<AtlasVaultRecoveryExportResult> confirmRecoverySetup(
    String recoveryKeyText,
  ) {
    return _retain(() async {
      final pending = _pendingRecoveryKey;
      if (pending == null) {
        return _fixedResult(AtlasVaultRecoveryExportDisposition.failed);
      }
      AtlasVaultRecoveryKey? supplied;
      Uint8List? expectedBytes;
      Uint8List? suppliedBytes;
      try {
        supplied = AtlasVaultRecoveryKey.parse(recoveryKeyText);
        expectedBytes = pending.copyBytes();
        suppliedBytes = supplied.copyBytes();
        if (!_constantTimeEquals(expectedBytes, suppliedBytes)) {
          return _fixedResult(AtlasVaultRecoveryExportDisposition.failed);
        }
        return await _withAuthorizedSession((session) async {
          final current = session.localStore;
          if (_recoveryWraps(current.vaultMetadata).isNotEmpty) {
            return _fixedResult(
              AtlasVaultRecoveryExportDisposition.recoveryRequired,
              count: current.records.length,
              recoveryWrapPresent: true,
            );
          }

          Uint8List? vaultKey;
          Uint8List? salt;
          Uint8List? nonce;
          try {
            vaultKey = session.copyVaultKey();
            salt = Uint8List.fromList(_recoverySaltProvider());
            nonce = Uint8List.fromList(_recoveryNonceProvider());
            final wrap = await wrapAtlasVaultKeyWithRecoveryV2(
              vaultKey: vaultKey,
              recoveryKey: pending,
              vaultId: session.vaultId,
              salt: salt,
              nonce: nonce,
            );
            final metadata = AtlasVaultMetadata.fromJson(<String, Object?>{
              ...current.vaultMetadata.toJson(),
              'key_wraps': <Object?>[
                for (final existing in current.vaultMetadata.keyWraps)
                  existing.toJson(),
                wrap.toJson(),
              ],
            });
            final updated = AtlasVaultLocalStore.fromJson(<String, Object?>{
              ...current.toJson(),
              'updated_at': _utcSeconds(_now()),
              'vault_metadata': metadata.toJson(),
            });
            final currentBytes = current.canonicalBytes();
            try {
              final expectedSha256 = await atlasVaultSha256Hex(currentBytes);
              await session.replaceLocalStore(
                updated,
                expectedSha256: expectedSha256,
              );
            } finally {
              _wipe(currentBytes);
            }

            final committed = await session.readCurrentLocalStore();
            if (committed != updated ||
                !_recordsEqual(current.records, committed.records)) {
              throw const AtlasVaultInteroperabilityException();
            }
            return await _prepareExport(
              store: committed,
              vaultKey: vaultKey,
              recoveryKey: pending,
            );
          } finally {
            _wipe(vaultKey);
            _wipe(salt);
            _wipe(nonce);
          }
        });
      } catch (_) {
        return _fixedResult(AtlasVaultRecoveryExportDisposition.failed);
      } finally {
        supplied?.destroy();
        _wipe(expectedBytes);
        _wipe(suppliedBytes);
        _clearPendingRecovery();
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryExportResult> prepareExistingRecoveryExport(
    String recoveryKeyText,
  ) {
    return _retain(() async {
      AtlasVaultRecoveryKey? recoveryKey;
      Uint8List? vaultKey;
      try {
        recoveryKey = AtlasVaultRecoveryKey.parse(recoveryKeyText);
        return await _withAuthorizedSession((session) async {
          final current = session.localStore;
          final wraps = _recoveryWraps(current.vaultMetadata);
          if (wraps.length != 1) {
            return _fixedResult(
              AtlasVaultRecoveryExportDisposition.recoveryRequired,
              count: current.records.length,
              recoveryWrapPresent: wraps.isNotEmpty,
            );
          }
          vaultKey = session.copyVaultKey();
          return await _prepareExport(
            store: current,
            vaultKey: vaultKey!,
            recoveryKey: recoveryKey!,
          );
        });
      } catch (_) {
        return _fixedResult(AtlasVaultRecoveryExportDisposition.failed);
      } finally {
        recoveryKey?.destroy();
        _wipe(vaultKey);
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryExportResult> savePreparedExport() {
    return _retain(() async {
      final prepared = _preparedExportBytes;
      if (prepared == null) {
        throw const AtlasVaultInteroperabilityException();
      }
      final bytes = Uint8List.fromList(prepared);
      final recordCount = _preparedRecordCount;
      _clearPreparedExport();
      try {
        final saved = await _documentTransport.saveEncryptedExport(bytes);
        return _fixedResult(
          saved
              ? AtlasVaultRecoveryExportDisposition.saved
              : AtlasVaultRecoveryExportDisposition.cancelled,
          count: recordCount,
          recoveryWrapPresent: true,
        );
      } catch (_) {
        return _fixedResult(
          AtlasVaultRecoveryExportDisposition.failed,
          count: recordCount,
          recoveryWrapPresent: true,
        );
      } finally {
        _wipe(bytes);
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryImportResult> inspectRecoveryImport() {
    return _retain(() async {
      final dependencies = _importDependencies;
      if (dependencies == null) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.unavailable,
        );
      }
      try {
        final journal = await _loadImportJournal(dependencies.journalStore);
        _recoveryImportPendingDidChange(journal != null);
        if (journal != null) {
          return _fixedImportResult(
            journal.stage == AtlasVaultRecoveryImportStage.completionPending
                ? AtlasVaultRecoveryImportDisposition.completionPending
                : AtlasVaultRecoveryImportDisposition.resumeRequired,
            pendingImport: true,
          );
        }
        final gate = await _cleanInstallGate(dependencies, journal: null);
        return _fixedImportResult(_gateDisposition(gate));
      } catch (_) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.unavailable,
        );
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryImportResult> prepareRecoveryImport() {
    return _retain(() async {
      _clearPreparedImport();
      final dependencies = _importDependencies;
      if (dependencies == null) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.unavailable,
        );
      }
      Uint8List? selectedBytes;
      Uint8List? canonicalBytes;
      AtlasVaultRecoveryImportJournal? journal;
      try {
        journal = await _loadImportJournal(dependencies.journalStore);
        if (journal == null) {
          _recoveryImportPendingDidChange(false);
        }
        final gate = await _cleanInstallGate(dependencies, journal: journal);
        if (gate != _ImportGate.ready) {
          return _fixedImportResult(
            _gateDisposition(gate),
            pendingImport: journal != null,
          );
        }

        selectedBytes = await _documentTransport.pickEncryptedExport();
        if (selectedBytes == null) {
          return _fixedImportResult(
            AtlasVaultRecoveryImportDisposition.cancelled,
            pendingImport: journal != null,
          );
        }
        if (selectedBytes.isEmpty ||
            selectedBytes.length > _maximumDocumentByteCount) {
          throw const AtlasVaultInteroperabilityException();
        }
        final export = AtlasVaultEncryptedExport.decodeJson(
          utf8.decode(selectedBytes, allowMalformed: false),
        );
        _requireSingleRecoveryWrap(export.vaultMetadata);
        _requireUniqueRecordIds(export.records);
        canonicalBytes = export.canonicalBytes();
        if (canonicalBytes.isEmpty ||
            canonicalBytes.length > _maximumDocumentByteCount) {
          throw const AtlasVaultInteroperabilityException();
        }
        if (!_constantTimeEquals(selectedBytes, canonicalBytes)) {
          throw const AtlasVaultInteroperabilityException();
        }
        final digest = await atlasVaultSha256Hex(canonicalBytes);
        if (journal != null) {
          final localStore = _localStoreForImport(
            export,
            storeId: journal.storeId,
            timestamp: journal.createdAt,
          );
          final localBytes = localStore.canonicalBytes();
          try {
            if (journal.exportId != export.exportId ||
                journal.vaultId != export.vaultMetadata.vaultId ||
                journal.exportSha256 != digest ||
                journal.localStoreSha256 !=
                    await atlasVaultSha256Hex(localBytes)) {
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.recoveryRequired,
                pendingImport: true,
              );
            }
          } finally {
            _wipe(localBytes);
          }
        }
        _preparedImport = export;
        _preparedImportBytes = Uint8List.fromList(canonicalBytes);
        _preparedImportSha256 = digest;
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.importPrepared,
          count: export.records.length,
          pendingImport: journal != null,
        );
      } catch (_) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.failed,
          pendingImport: journal != null,
        );
      } finally {
        _wipe(selectedBytes);
        _wipe(canonicalBytes);
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryImportResult> confirmRecoveryImport(
    String recoveryKeyText,
  ) {
    return _retain(() async {
      final dependencies = _importDependencies;
      final export = _preparedImport;
      final exportSha256 = _preparedImportSha256;
      if (dependencies == null || export == null || exportSha256 == null) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.recoveryRequired,
        );
      }
      AtlasVaultRecoveryKey? recoveryKey;
      Uint8List? vaultKey;
      AtlasVaultRecoveryImportJournal? journal;
      var importAdmissionHeld = false;
      try {
        recoveryKey = AtlasVaultRecoveryKey.parse(recoveryKeyText);
        final wrap = _requireSingleRecoveryWrap(export.vaultMetadata);
        vaultKey = await unwrapAtlasVaultRecoveryWrapV2(
          wrap: wrap,
          recoveryKey: recoveryKey,
          vaultId: export.vaultMetadata.vaultId,
        );
        if (vaultKey.length != 32) {
          throw const AtlasVaultInteroperabilityException();
        }
        final verification = await _verifyImportRecords(
          export: export,
          vaultKey: vaultKey,
        );
        recoveryKey.destroy();
        recoveryKey = null;
        return await _importTransactionAdmission.runRecoveryImportTransaction(
          () async {
            journal = await _loadImportJournal(dependencies.journalStore);
            if (journal == null) {
              await _importOperationAdmission.beginRecoveryImportAdmission();
              importAdmissionHeld = true;
            }
            final gate = await _cleanInstallGate(
              dependencies,
              journal: journal,
            );
            if (gate != _ImportGate.ready) {
              return _fixedImportResult(
                _gateDisposition(gate),
                count: export.records.length,
                pendingImport: journal != null,
              );
            }

            final timestamp = journal?.createdAt ?? _utcSeconds(_now());
            final storeId = journal?.storeId ?? _importStoreIdProvider();
            final localStore = _localStoreForImport(
              export,
              storeId: storeId,
              timestamp: timestamp,
            );
            if (journal == null) {
              await _requireImportTargetAvailable(
                dependencies,
                export.vaultMetadata.vaultId,
              );
            }
            await _runtime.validateImportProjection(
              vaultId: export.vaultMetadata.vaultId,
              vaultKey: vaultKey!,
              store: localStore,
            );
            final localBytes = localStore.canonicalBytes();
            try {
              final localStoreSha256 = await atlasVaultSha256Hex(localBytes);
              final vaultKeySha256 = await atlasVaultSha256Hex(vaultKey);
              if (journal == null) {
                journal = AtlasVaultRecoveryImportJournal.prepared(
                  profile: _recoveryImportProfile,
                  importId: _importIdProvider(),
                  exportId: export.exportId,
                  vaultId: export.vaultMetadata.vaultId,
                  storeId: storeId,
                  createdAt: timestamp,
                  exportSha256: exportSha256,
                  localStoreSha256: localStoreSha256,
                  vaultKeySha256: vaultKeySha256,
                );
                await _createAndVerifyImportJournal(
                  dependencies.journalStore,
                  journal!,
                );
              } else if (journal!.exportId != export.exportId ||
                  journal!.vaultId != export.vaultMetadata.vaultId ||
                  journal!.storeId != storeId ||
                  journal!.exportSha256 != exportSha256 ||
                  journal!.localStoreSha256 != localStoreSha256 ||
                  journal!.vaultKeySha256 != vaultKeySha256) {
                return _fixedImportResult(
                  AtlasVaultRecoveryImportDisposition.recoveryRequired,
                  count: export.records.length,
                  pendingImport: true,
                );
              }

              final pendingJournal = await _installImport(
                dependencies: dependencies,
                journal: journal!,
                localStore: localStore,
                vaultKey: vaultKey,
                verification: verification,
              );
              if (pendingJournal != null) {
                return _fixedImportResult(
                  AtlasVaultRecoveryImportDisposition.completionPending,
                  count: export.records.length,
                  pendingImport: true,
                );
              }
              _clearPreparedImport();
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.importedAndActive,
                count: export.records.length,
              );
            } finally {
              _wipe(localBytes);
            }
          },
        );
      } catch (_) {
        AtlasVaultRecoveryImportJournal? recoveredJournal;
        var journalReadWasConclusive = false;
        try {
          recoveredJournal = await _loadImportJournal(
            dependencies.journalStore,
          );
          journalReadWasConclusive = true;
        } catch (_) {
          // The locally known journal remains authoritative until a later
          // successful read proves absence.
        }
        final pendingJournal =
            recoveredJournal ?? (journalReadWasConclusive ? null : journal);
        if (pendingJournal != null) {
          _recoveryImportPendingDidChange(true);
        } else if (journalReadWasConclusive) {
          _recoveryImportPendingDidChange(false);
        }
        return _fixedImportResult(
          pendingJournal == null
              ? AtlasVaultRecoveryImportDisposition.failed
              : pendingJournal.stage ==
                    AtlasVaultRecoveryImportStage.completionPending
              ? AtlasVaultRecoveryImportDisposition.completionPending
              : AtlasVaultRecoveryImportDisposition.recoveryRequired,
          count: export.records.length,
          pendingImport: pendingJournal != null,
        );
      } finally {
        if (importAdmissionHeld) {
          _importOperationAdmission.endRecoveryImportAdmission();
        }
        recoveryKey?.destroy();
        _wipe(vaultKey);
      }
    });
  }

  @override
  Future<AtlasVaultRecoveryImportResult> discardPendingImport() {
    return _retain(() async {
      _clearPreparedImport();
      final dependencies = _importDependencies;
      if (dependencies == null) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.unavailable,
        );
      }
      Uint8List? key;
      try {
        return await _importTransactionAdmission.runRecoveryImportTransaction(
          () async {
            final journal = await _loadImportJournal(dependencies.journalStore);
            if (journal == null) {
              _recoveryImportPendingDidChange(false);
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.cancelled,
              );
            }
            if (journal.stage.index >=
                AtlasVaultRecoveryImportStage.selectionCommitted.index) {
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.recoveryRequired,
                pendingImport: true,
              );
            }
            if (await _selectedVaultStore.read() != null) {
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.recoveryRequired,
                pendingImport: true,
              );
            }

            final store = await dependencies.localStoreIO.read(journal.vaultId);
            if (store != null) {
              final bytes = store.canonicalBytes();
              try {
                if (await atlasVaultSha256Hex(bytes) !=
                    journal.localStoreSha256) {
                  return _fixedImportResult(
                    AtlasVaultRecoveryImportDisposition.recoveryRequired,
                    pendingImport: true,
                  );
                }
              } finally {
                _wipe(bytes);
              }
            }
            key = await dependencies.secureKeyStore.loadVaultKey(
              journal.vaultId,
            );
            if (key != null &&
                await atlasVaultSha256Hex(key!) != journal.vaultKeySha256) {
              return _fixedImportResult(
                AtlasVaultRecoveryImportDisposition.recoveryRequired,
                pendingImport: true,
              );
            }

            if (store != null) {
              await dependencies.localStoreIO.delete(journal.vaultId);
            }
            if (key != null) {
              await dependencies.secureKeyStore.deleteVaultKey(journal.vaultId);
            }
            if (await dependencies.localStoreIO.read(journal.vaultId) != null ||
                await dependencies.secureKeyStore.containsVaultKey(
                  journal.vaultId,
                )) {
              throw const AtlasVaultInteroperabilityException();
            }
            await _deleteImportJournal(dependencies.journalStore, journal);
            return _fixedImportResult(
              AtlasVaultRecoveryImportDisposition.cancelled,
            );
          },
        );
      } catch (_) {
        return _fixedImportResult(
          AtlasVaultRecoveryImportDisposition.recoveryRequired,
          pendingImport: true,
        );
      } finally {
        _wipe(key);
      }
    });
  }

  @override
  void discardPendingRecovery() {
    if (_operation != null) {
      _discardWhenIdle = true;
      return;
    }
    _clearPendingRecovery();
    _clearPreparedExport();
    _clearPreparedImport();
  }

  @override
  Future<void> stop() async {
    if (_stopped) {
      await _operation;
      return;
    }
    _stopped = true;
    _generation += 1;
    await _operation;
    _clearPendingRecovery();
    _clearPreparedExport();
    _clearPreparedImport();
  }

  Future<T> _withAuthorizedSession<T>(
    Future<T> Function(AtlasVaultInteroperabilitySession session) body,
  ) {
    return _runtime.withInteroperabilitySession((session) async {
      Uint8List? migrationBytes;
      try {
        final selected = await _selectedVaultStore.read();
        if (selected != session.vaultId) {
          throw const AtlasVaultInteroperabilityException();
        }
        migrationBytes = await _migrationJournalStore.read();
        if (migrationBytes != null || await _recoveryImportPending()) {
          throw const AtlasVaultInteroperabilityException();
        }
        return await body(session);
      } finally {
        _wipe(migrationBytes);
      }
    });
  }

  Future<AtlasVaultRecoveryExportResult> _prepareExport({
    required AtlasVaultLocalStore store,
    required Uint8List vaultKey,
    required AtlasVaultRecoveryKey recoveryKey,
  }) async {
    _clearPreparedExport();
    await _verifyStore(
      store: store,
      vaultKey: vaultKey,
      recoveryKey: recoveryKey,
    );
    final export = AtlasVaultEncryptedExport.fromJson(<String, Object?>{
      'format': AtlasVaultEncryptedExport.format,
      'version': AtlasVaultEncryptedExport.version,
      'export_id': _uuidProvider(),
      'created_at': _utcSeconds(_now()),
      'vault_metadata': store.vaultMetadata.toJson(),
      'records': <Object?>[for (final record in store.records) record.toJson()],
    });
    final bytes = export.canonicalBytes();
    Uint8List? reencoded;
    try {
      final decoded = AtlasVaultEncryptedExport.decodeJson(
        utf8.decode(bytes, allowMalformed: false),
      );
      reencoded = decoded.canonicalBytes();
      final digest = await atlasVaultSha256Hex(bytes);
      if (!_constantTimeEquals(bytes, reencoded) ||
          !RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) {
        throw const AtlasVaultInteroperabilityException();
      }
      _preparedExportBytes = Uint8List.fromList(bytes);
      _preparedRecordCount = store.records.length;
      return _fixedResult(
        AtlasVaultRecoveryExportDisposition.exportReady,
        count: store.records.length,
        recoveryWrapPresent: true,
      );
    } finally {
      _wipe(bytes);
      _wipe(reencoded);
    }
  }

  Future<void> _verifyStore({
    required AtlasVaultLocalStore store,
    required Uint8List vaultKey,
    required AtlasVaultRecoveryKey recoveryKey,
  }) async {
    final wraps = _recoveryWraps(store.vaultMetadata);
    if (wraps.length != 1) {
      throw const AtlasVaultInteroperabilityException();
    }
    Uint8List? recovered;
    try {
      recovered = await unwrapAtlasVaultRecoveryWrapV2(
        wrap: wraps.single,
        recoveryKey: recoveryKey,
        vaultId: store.vaultMetadata.vaultId,
      );
      if (!_constantTimeEquals(recovered, vaultKey)) {
        throw const AtlasVaultInteroperabilityException();
      }
    } finally {
      _wipe(recovered);
    }

    for (final record in store.records) {
      Uint8List? plaintext;
      try {
        plaintext = await openAtlasVaultRecord(
          vaultKey: vaultKey,
          vaultId: store.vaultMetadata.vaultId,
          record: record,
        );
        if (!record.deleted) {
          AtlasVaultPayloadEnvelope.decodeJson(
            utf8.decode(plaintext, allowMalformed: false),
          );
        }
      } finally {
        _wipe(plaintext);
      }
    }
  }

  _ImportDependencies? get _importDependencies {
    final journalStore = _recoveryImportJournalStore;
    final secureKeyStore = _secureKeyStore;
    final localStoreIO = _localStoreIO;
    final inMemorySource = _inMemorySource;
    final compatibilitySource = _compatibilitySource;
    final cacheSource = _cacheSource;
    if (journalStore == null ||
        secureKeyStore == null ||
        localStoreIO == null ||
        inMemorySource == null ||
        compatibilitySource == null ||
        cacheSource == null) {
      return null;
    }
    return _ImportDependencies(
      journalStore: journalStore,
      secureKeyStore: secureKeyStore,
      localStoreIO: localStoreIO,
      inMemorySource: inMemorySource,
      compatibilitySource: compatibilitySource,
      cacheSource: cacheSource,
    );
  }

  Future<_ImportGate> _cleanInstallGate(
    _ImportDependencies dependencies, {
    required AtlasVaultRecoveryImportJournal? journal,
  }) async {
    Uint8List? migrationBytes;
    try {
      migrationBytes = await _migrationJournalStore.read();
      if (migrationBytes != null) {
        return _ImportGate.migrationRequired;
      }
      final selected = await _selectedVaultStore.read();
      if (journal == null) {
        if (selected != null || _runtime.isActive) {
          return _ImportGate.existingVault;
        }
      } else if (journal.stage.index <
          AtlasVaultRecoveryImportStage.selectionCommitted.index) {
        final selectionCreateWasInterrupted =
            journal.stage == AtlasVaultRecoveryImportStage.keyCreated &&
            selected == journal.vaultId;
        if ((selected != null && !selectionCreateWasInterrupted) ||
            _runtime.isActive) {
          return _ImportGate.recoveryRequired;
        }
      } else {
        if (selected != journal.vaultId ||
            (_runtime.isActive && !_runtime.isActiveVault(journal.vaultId))) {
          return _ImportGate.recoveryRequired;
        }
      }

      final cache = await dependencies.cacheSource
          .readPrivateStateForMigration();
      if (cache.containsPrivateState) {
        return _ImportGate.migrationRequired;
      }
      if (journal == null) {
        final compatibility = await dependencies.compatibilitySource
            .readCompatibilityPrivateState();
        if (compatibility.savedSearches.isNotEmpty ||
            compatibility.trackerRecords.isNotEmpty) {
          return _ImportGate.migrationRequired;
        }
      }
      if (journal == null ||
          journal.stage.index <
              AtlasVaultRecoveryImportStage.selectionCommitted.index ||
          !_runtime.isActiveVault(journal.vaultId)) {
        final memory = await dependencies.inMemorySource
            .readPlaintextPrivateState();
        if (memory.savedSearches.isNotEmpty ||
            memory.trackerRecords.isNotEmpty) {
          return _ImportGate.migrationRequired;
        }
      }
      return _ImportGate.ready;
    } catch (_) {
      return _ImportGate.unavailable;
    } finally {
      _wipe(migrationBytes);
    }
  }

  Future<void> _requireImportTargetAvailable(
    _ImportDependencies dependencies,
    String vaultId,
  ) async {
    if (await dependencies.localStoreIO.read(vaultId) != null ||
        await dependencies.secureKeyStore.containsVaultKey(vaultId)) {
      throw const AtlasVaultInteroperabilityException();
    }
  }

  AtlasVaultRecoveryImportDisposition _gateDisposition(_ImportGate gate) {
    return switch (gate) {
      _ImportGate.ready => AtlasVaultRecoveryImportDisposition.ready,
      _ImportGate.migrationRequired =>
        AtlasVaultRecoveryImportDisposition.migrationRequired,
      _ImportGate.existingVault =>
        AtlasVaultRecoveryImportDisposition.existingVault,
      _ImportGate.recoveryRequired =>
        AtlasVaultRecoveryImportDisposition.recoveryRequired,
      _ImportGate.unavailable =>
        AtlasVaultRecoveryImportDisposition.unavailable,
    };
  }

  AtlasVaultRecoveryKeyWrapV2 _requireSingleRecoveryWrap(
    AtlasVaultMetadata metadata,
  ) {
    final wraps = _recoveryWraps(metadata);
    if (wraps.length != 1) {
      throw const AtlasVaultInteroperabilityException();
    }
    return wraps.single;
  }

  void _requireUniqueRecordIds(List<AtlasVaultEncryptedRecord> records) {
    final identifiers = <String>{};
    for (final record in records) {
      if (!identifiers.add(record.id)) {
        throw const AtlasVaultInteroperabilityException();
      }
    }
  }

  Future<_ImportVerification> _verifyImportRecords({
    required AtlasVaultEncryptedExport export,
    required Uint8List vaultKey,
  }) async {
    var savedSearchCount = 0;
    var trackerCount = 0;
    for (final record in export.records) {
      Uint8List? plaintext;
      try {
        plaintext = await openAtlasVaultRecord(
          vaultKey: vaultKey,
          vaultId: export.vaultMetadata.vaultId,
          record: record,
        );
        if (record.deleted) {
          if (plaintext.isNotEmpty) {
            throw const AtlasVaultInteroperabilityException();
          }
          continue;
        }
        final payload = AtlasVaultPayloadEnvelope.decodeJson(
          utf8.decode(plaintext, allowMalformed: false),
        );
        switch (payload.type) {
          case AtlasVaultPayloadType.savedSearch:
            savedSearchCount += 1;
          case AtlasVaultPayloadType.savedJob:
            trackerCount += 1;
          case AtlasVaultPayloadType.applicationNote:
          case AtlasVaultPayloadType.profileSnippet:
          case AtlasVaultPayloadType.draftMetadata:
            break;
        }
      } finally {
        _wipe(plaintext);
      }
    }
    return _ImportVerification(
      savedSearchCount: savedSearchCount,
      trackerCount: trackerCount,
    );
  }

  AtlasVaultLocalStore _localStoreForImport(
    AtlasVaultEncryptedExport export, {
    required String storeId,
    required String timestamp,
  }) {
    return AtlasVaultLocalStore.fromJson(<String, Object?>{
      'format': AtlasVaultLocalStore.format,
      'version': AtlasVaultLocalStore.version,
      'store_id': storeId,
      'created_at': timestamp,
      'updated_at': timestamp,
      'vault_metadata': export.vaultMetadata.toJson(),
      'records': <Object?>[
        for (final record in export.records) record.toJson(),
      ],
    });
  }

  Future<AtlasVaultRecoveryImportJournal?> _installImport({
    required _ImportDependencies dependencies,
    required AtlasVaultRecoveryImportJournal journal,
    required AtlasVaultLocalStore localStore,
    required Uint8List vaultKey,
    required _ImportVerification verification,
  }) async {
    var current = journal;

    if (current.stage == AtlasVaultRecoveryImportStage.prepared) {
      await _ensureImportStore(dependencies, current, localStore);
      current = await _transitionImportJournal(
        dependencies.journalStore,
        current,
        AtlasVaultRecoveryImportStage.storeCreated,
      );
    } else {
      await _verifyImportStore(dependencies, current);
    }

    if (current.stage == AtlasVaultRecoveryImportStage.storeCreated) {
      await _ensureImportKey(dependencies, current, vaultKey);
      current = await _transitionImportJournal(
        dependencies.journalStore,
        current,
        AtlasVaultRecoveryImportStage.keyCreated,
      );
    } else {
      await _verifyImportKey(dependencies, current);
    }

    if (current.stage == AtlasVaultRecoveryImportStage.keyCreated) {
      await _ensureImportSelection(current);
      current = await _transitionImportJournal(
        dependencies.journalStore,
        current,
        AtlasVaultRecoveryImportStage.selectionCommitted,
      );
    } else {
      await _verifyImportSelection(current);
    }

    if (current.stage == AtlasVaultRecoveryImportStage.selectionCommitted) {
      await _activateAndVerifyImport(current, verification);
      current = await _transitionImportJournal(
        dependencies.journalStore,
        current,
        AtlasVaultRecoveryImportStage.completionPending,
      );
    } else {
      await _activateAndVerifyImport(current, verification);
    }

    try {
      await _deleteImportJournal(dependencies.journalStore, current);
      return null;
    } catch (_) {
      return current;
    }
  }

  Future<void> _ensureImportStore(
    _ImportDependencies dependencies,
    AtlasVaultRecoveryImportJournal journal,
    AtlasVaultLocalStore expected,
  ) async {
    final current = await dependencies.localStoreIO.read(journal.vaultId);
    if (current == null) {
      await dependencies.localStoreIO.create(journal.vaultId, expected);
    }
    await _verifyImportStore(dependencies, journal);
  }

  Future<void> _verifyImportStore(
    _ImportDependencies dependencies,
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    final restored = await dependencies.localStoreIO.read(journal.vaultId);
    if (restored == null || restored.storeId != journal.storeId) {
      throw const AtlasVaultInteroperabilityException();
    }
    final bytes = restored.canonicalBytes();
    try {
      if (await atlasVaultSha256Hex(bytes) != journal.localStoreSha256) {
        throw const AtlasVaultInteroperabilityException();
      }
    } finally {
      _wipe(bytes);
    }
  }

  Future<void> _ensureImportKey(
    _ImportDependencies dependencies,
    AtlasVaultRecoveryImportJournal journal,
    Uint8List vaultKey,
  ) async {
    final current = await dependencies.secureKeyStore.loadVaultKey(
      journal.vaultId,
    );
    if (current == null) {
      await dependencies.secureKeyStore.createVaultKey(
        journal.vaultId,
        vaultKey,
      );
    } else {
      _wipe(current);
    }
    await _verifyImportKey(dependencies, journal);
  }

  Future<void> _verifyImportKey(
    _ImportDependencies dependencies,
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    final restored = await dependencies.secureKeyStore.loadVaultKey(
      journal.vaultId,
    );
    if (restored == null) {
      throw const AtlasVaultInteroperabilityException();
    }
    try {
      if (await atlasVaultSha256Hex(restored) != journal.vaultKeySha256) {
        throw const AtlasVaultInteroperabilityException();
      }
    } finally {
      _wipe(restored);
    }
  }

  Future<void> _ensureImportSelection(
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    final selected = await _selectedVaultStore.read();
    if (selected == null) {
      await _selectedVaultStore.create(journal.vaultId);
    } else if (selected != journal.vaultId) {
      throw const AtlasVaultInteroperabilityException();
    }
    await _verifyImportSelection(journal);
  }

  Future<void> _verifyImportSelection(
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    if (await _selectedVaultStore.read() != journal.vaultId) {
      throw const AtlasVaultInteroperabilityException();
    }
  }

  Future<void> _activateAndVerifyImport(
    AtlasVaultRecoveryImportJournal journal,
    _ImportVerification verification,
  ) async {
    if (!_runtime.isActiveVault(journal.vaultId)) {
      if (_runtime.isActive || !await _activateImportedVault(journal.vaultId)) {
        throw const AtlasVaultInteroperabilityException();
      }
    }
    if (!_runtime.isActiveVault(journal.vaultId)) {
      throw const AtlasVaultInteroperabilityException();
    }
    final snapshot = await _runtime.read();
    if (snapshot.savedSearches.length != verification.savedSearchCount ||
        snapshot.trackerRecords.length != verification.trackerCount) {
      throw const AtlasVaultInteroperabilityException();
    }
  }

  Future<void> _createAndVerifyImportJournal(
    AtlasVaultProtectedRecoveryImportJournalStore store,
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    final bytes = journal.canonicalBytes();
    try {
      await store.create(bytes);
      final restored = await _loadImportJournal(store);
      if (restored == null || !_sameImportJournal(restored, journal)) {
        throw const AtlasVaultInteroperabilityException();
      }
      _recoveryImportPendingDidChange(true);
    } finally {
      _wipe(bytes);
    }
  }

  Future<AtlasVaultRecoveryImportJournal> _transitionImportJournal(
    AtlasVaultProtectedRecoveryImportJournalStore store,
    AtlasVaultRecoveryImportJournal current,
    AtlasVaultRecoveryImportStage next,
  ) async {
    final updated = current.transitionedTo(next);
    final currentBytes = current.canonicalBytes();
    final updatedBytes = updated.canonicalBytes();
    try {
      await store.replace(
        updatedBytes,
        expectedSha256: await atlasVaultSha256Hex(currentBytes),
      );
      final restored = await _loadImportJournal(store);
      if (restored == null || !_sameImportJournal(restored, updated)) {
        throw const AtlasVaultInteroperabilityException();
      }
      return updated;
    } finally {
      _wipe(currentBytes);
      _wipe(updatedBytes);
    }
  }

  Future<void> _deleteImportJournal(
    AtlasVaultProtectedRecoveryImportJournalStore store,
    AtlasVaultRecoveryImportJournal journal,
  ) async {
    final bytes = journal.canonicalBytes();
    Uint8List? restored;
    try {
      await store.delete(expectedSha256: await atlasVaultSha256Hex(bytes));
      restored = await store.read();
      if (restored != null) {
        throw const AtlasVaultInteroperabilityException();
      }
      _recoveryImportPendingDidChange(false);
    } finally {
      _wipe(bytes);
      _wipe(restored);
    }
  }

  Future<AtlasVaultRecoveryImportJournal?> _loadImportJournal(
    AtlasVaultProtectedRecoveryImportJournalStore store,
  ) async {
    final bytes = await store.read();
    if (bytes == null) {
      return null;
    }
    try {
      return AtlasVaultRecoveryImportJournal.decodeBytes(
        bytes,
        profile: _recoveryImportProfile,
      );
    } finally {
      _wipe(bytes);
    }
  }

  bool _sameImportJournal(
    AtlasVaultRecoveryImportJournal left,
    AtlasVaultRecoveryImportJournal right,
  ) {
    return canonicalJsonString(left.toJson()) ==
        canonicalJsonString(right.toJson());
  }

  List<AtlasVaultRecoveryKeyWrapV2> _recoveryWraps(
    AtlasVaultMetadata metadata,
  ) {
    return metadata.keyWraps.whereType<AtlasVaultRecoveryKeyWrapV2>().toList(
      growable: false,
    );
  }

  AtlasVaultRecoveryExportResult _fixedResult(
    AtlasVaultRecoveryExportDisposition disposition, {
    int count = 0,
    bool recoveryWrapPresent = false,
  }) {
    return AtlasVaultRecoveryExportResult(
      disposition: disposition,
      encryptedRecordCount: count,
      recoveryWrapPresent: recoveryWrapPresent,
    );
  }

  AtlasVaultRecoveryImportResult _fixedImportResult(
    AtlasVaultRecoveryImportDisposition disposition, {
    int count = 0,
    bool pendingImport = false,
  }) {
    return AtlasVaultRecoveryImportResult(
      disposition: disposition,
      encryptedRecordCount: count,
      pendingImport: pendingImport,
    );
  }

  Future<T> _retain<T>(Future<T> Function() body) {
    if (_stopped || _operation != null) {
      return Future<T>.error(const AtlasVaultInteroperabilityException());
    }
    final generation = _generation;
    final completer = Completer<T>();
    final start = Completer<void>();
    late final Future<void> retained;
    retained = start.future.then((_) async {
      try {
        final value = await body();
        if (_stopped || generation != _generation) {
          throw const AtlasVaultInteroperabilityException();
        }
        completer.complete(value);
      } catch (_) {
        if (!completer.isCompleted) {
          completer.completeError(const AtlasVaultInteroperabilityException());
        }
      } finally {
        if (_discardWhenIdle) {
          _discardWhenIdle = false;
          _clearPendingRecovery();
          _clearPreparedExport();
          _clearPreparedImport();
        }
        if (identical(_operation, retained)) {
          _operation = null;
        }
      }
    });
    _operation = retained;
    start.complete();
    return completer.future;
  }

  void _clearPendingRecovery() {
    _pendingRecoveryKey?.destroy();
    _pendingRecoveryKey = null;
  }

  void _clearPreparedExport() {
    _wipe(_preparedExportBytes);
    _preparedExportBytes = null;
    _preparedRecordCount = 0;
  }

  void _clearPreparedImport() {
    _wipe(_preparedImportBytes);
    _preparedImportBytes = null;
    _preparedImport = null;
    _preparedImportSha256 = null;
  }

  static bool _recordsEqual(
    List<AtlasVaultEncryptedRecord> left,
    List<AtlasVaultEncryptedRecord> right,
  ) {
    if (left.length != right.length) {
      return false;
    }
    for (var index = 0; index < left.length; index += 1) {
      final leftBytes = utf8.encode(jsonEncode(left[index].toJson()));
      final rightBytes = utf8.encode(jsonEncode(right[index].toJson()));
      if (!_constantTimeEquals(leftBytes, rightBytes)) {
        return false;
      }
    }
    return true;
  }

  static bool _constantTimeEquals(List<int> left, List<int> right) {
    var difference = left.length ^ right.length;
    final count = left.length > right.length ? left.length : right.length;
    for (var index = 0; index < count; index += 1) {
      final leftValue = index < left.length ? left[index] : 0;
      final rightValue = index < right.length ? right[index] : 0;
      difference |= leftValue ^ rightValue;
    }
    return difference == 0;
  }

  static String _utcSeconds(DateTime value) {
    final utc = value.toUtc();
    String two(int number) => number.toString().padLeft(2, '0');
    return '${utc.year.toString().padLeft(4, '0')}-'
        '${two(utc.month)}-${two(utc.day)}T'
        '${two(utc.hour)}:${two(utc.minute)}:${two(utc.second)}Z';
  }

  static String _secureUuidV4() {
    final bytes = _secureBytes(16);
    try {
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      final hex = bytes
          .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
          .join();
      return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
          '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
          '${hex.substring(20)}';
    } finally {
      _wipe(bytes);
    }
  }

  static Uint8List _secureBytes(int count) {
    final random = Random.secure();
    return Uint8List.fromList(
      List<int>.generate(count, (_) => random.nextInt(256)),
    );
  }

  static void _wipe(Uint8List? value) {
    value?.fillRange(0, value.length, 0);
  }

  static void _ignoreImportPendingChange(bool _) {}

  @override
  String toString() => 'AtlasVaultInteroperabilityCoordinator(<redacted>)';
}

enum _ImportGate {
  ready,
  migrationRequired,
  existingVault,
  recoveryRequired,
  unavailable,
}

final class _ImportDependencies {
  const _ImportDependencies({
    required this.journalStore,
    required this.secureKeyStore,
    required this.localStoreIO,
    required this.inMemorySource,
    required this.compatibilitySource,
    required this.cacheSource,
  });

  final AtlasVaultProtectedRecoveryImportJournalStore journalStore;
  final AtlasVaultMigrationSecureKeyStore secureKeyStore;
  final AtlasVaultLocalStoreIO localStoreIO;
  final AtlasVaultPlaintextStateSource inMemorySource;
  final AtlasVaultCompatibilityPrivateSource compatibilitySource;
  final AtlasLocalCacheMigrationSource cacheSource;
}

final class _ImportVerification {
  const _ImportVerification({
    required this.savedSearchCount,
    required this.trackerCount,
  });

  final int savedSearchCount;
  final int trackerCount;
}

final class _OneShotRecoveryDisplayHandle
    implements AtlasVaultRecoveryDisplayHandle {
  _OneShotRecoveryDisplayHandle(String value)
    : _codeUnits = Uint16List.fromList(value.codeUnits);

  Uint16List? _codeUnits;

  @override
  String? take() {
    final codeUnits = _codeUnits;
    if (codeUnits == null) {
      return null;
    }
    _codeUnits = null;
    try {
      return String.fromCharCodes(codeUnits);
    } finally {
      codeUnits.fillRange(0, codeUnits.length, 0);
    }
  }

  @override
  void destroy() {
    final codeUnits = _codeUnits;
    codeUnits?.fillRange(0, codeUnits.length, 0);
    _codeUnits = null;
  }

  @override
  String toString() => 'AtlasVaultRecoveryDisplayHandle(<redacted>)';
}
