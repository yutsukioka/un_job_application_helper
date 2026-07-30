import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'crypto.dart';
import 'export.dart';
import 'models.dart';
import 'payloads.dart';
import 'plaintext_migration.dart';
import 'private_state_runtime.dart';
import 'recovery.dart';

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

  void discardPendingRecovery();

  Future<void> stop();
}

final class AtlasVaultInteroperabilityCoordinator
    implements AtlasVaultInteroperabilityCoordinating {
  AtlasVaultInteroperabilityCoordinator({
    required AtlasVaultPrivateStateRuntime runtime,
    required AtlasVaultSelectedVaultStore selectedVaultStore,
    required AtlasVaultProtectedMigrationJournalStore migrationJournalStore,
    required Future<bool> Function() recoveryImportPending,
    required AtlasVaultEncryptedDocumentTransport documentTransport,
    DateTime Function()? now,
    String Function()? uuidProvider,
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
       _now = now ?? DateTime.now,
       _uuidProvider = uuidProvider ?? _secureUuidV4,
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
  final DateTime Function() _now;
  final String Function() _uuidProvider;
  final AtlasVaultRecoveryKey Function() _recoveryKeyProvider;
  final Uint8List Function() _recoverySaltProvider;
  final Uint8List Function() _recoveryNonceProvider;

  AtlasVaultRecoveryKey? _pendingRecoveryKey;
  Uint8List? _preparedExportBytes;
  int _preparedRecordCount = 0;
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
  void discardPendingRecovery() {
    if (_operation != null) {
      _discardWhenIdle = true;
      return;
    }
    _clearPendingRecovery();
    _clearPreparedExport();
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

  @override
  String toString() => 'AtlasVaultInteroperabilityCoordinator(<redacted>)';
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
