import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';

import 'canonical_json.dart';
import 'crypto.dart';
import 'device_identity.dart';
import 'key_delivery.dart';
import 'local_store_io.dart';
import 'models.dart';
import 'pairing.dart';
import 'plaintext_migration.dart';
import 'private_state_runtime.dart';
import 'protected_state_bounds.dart';
import 'strict_values.dart';
import 'trusted_devices.dart';

const _transactionFormat = 'atlasvault-pairing-transaction';
const _transactionVersion = 1;
const atlasVaultMaximumPairingStateByteCount =
    atlasVaultMaximumTrustedDeviceRegistryByteCount;
const atlasVaultMaximumPairingTransactionByteCount =
    atlasVaultMaximumPairingTransactionJournalByteCount;
const atlasVaultMaximumPairingArtifactByteCount =
    atlasVaultMaximumStagedPairingArtifactByteCount;
const _maximumTrustedPairingPeers = 64;
// Device-identity rotation is independent of the initial vault key.
const _initialVaultKeyEpoch = 1;

typedef AtlasVaultPairingKeyReleaseAuthorization =
    Future<bool> Function(String vaultId);

final class AtlasVaultPairingTransactionException implements Exception {
  const AtlasVaultPairingTransactionException();

  @override
  String toString() => 'AtlasVault pairing transaction is invalid.';
}

final class AtlasVaultPairingStorageException implements Exception {
  const AtlasVaultPairingStorageException();

  @override
  String toString() => 'AtlasVault pairing storage operation failed.';
}

enum AtlasVaultPairingRole {
  inviter,
  invitee;

  String get encoded => name;
}

enum AtlasVaultPairingStage {
  offerCreated('offer_created'),
  offerSaved('offer_saved'),
  offerImported('offer_imported'),
  acceptanceCreated('acceptance_created'),
  acceptanceSaved('acceptance_saved'),
  acceptanceImported('acceptance_imported'),
  sasConfirmed('sas_confirmed'),
  offerConsumed('offer_consumed'),
  deliveryCreated('delivery_created'),
  deliveryExportStarted('delivery_export_started'),
  deliverySaved('delivery_saved'),
  deliveryImported('delivery_imported'),
  storeCreated('store_created'),
  keyCreated('key_created'),
  selectionCommitted('selection_committed'),
  runtimeActivated('runtime_activated'),
  acknowledgementCreated('acknowledgement_created'),
  acknowledgementSaved('acknowledgement_saved'),
  acknowledgementImported('acknowledgement_imported'),
  acknowledgementConsumed('acknowledgement_consumed'),
  trustCommitted('trust_committed');

  const AtlasVaultPairingStage(this.encoded);

  final String encoded;
}

const _inviterStages = <AtlasVaultPairingStage>[
  AtlasVaultPairingStage.offerCreated,
  AtlasVaultPairingStage.offerSaved,
  AtlasVaultPairingStage.acceptanceImported,
  AtlasVaultPairingStage.sasConfirmed,
  AtlasVaultPairingStage.deliveryCreated,
  AtlasVaultPairingStage.deliveryExportStarted,
  AtlasVaultPairingStage.deliverySaved,
  AtlasVaultPairingStage.acknowledgementImported,
  AtlasVaultPairingStage.acknowledgementConsumed,
  AtlasVaultPairingStage.trustCommitted,
];

const _inviteeStages = <AtlasVaultPairingStage>[
  AtlasVaultPairingStage.offerImported,
  AtlasVaultPairingStage.acceptanceCreated,
  AtlasVaultPairingStage.acceptanceSaved,
  AtlasVaultPairingStage.sasConfirmed,
  AtlasVaultPairingStage.offerConsumed,
  AtlasVaultPairingStage.deliveryImported,
  AtlasVaultPairingStage.storeCreated,
  AtlasVaultPairingStage.keyCreated,
  AtlasVaultPairingStage.selectionCommitted,
  AtlasVaultPairingStage.runtimeActivated,
  AtlasVaultPairingStage.trustCommitted,
  AtlasVaultPairingStage.acknowledgementCreated,
  AtlasVaultPairingStage.acknowledgementSaved,
];

final class AtlasVaultStagedPairingArtifact {
  AtlasVaultStagedPairingArtifact._({
    required this.kind,
    required this.sha256,
    required this.byteCount,
  });

  final AtlasVaultPairingArtifactKind kind;
  final String sha256;
  final int byteCount;

  factory AtlasVaultStagedPairingArtifact.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{'kind', 'sha256', 'byte_count'},
        context: 'Staged pairing artifact',
      );
      final kindText = requireAtlasVaultString(
        value['kind'],
        field: 'kind',
        allowEmpty: false,
      );
      final byteCount = requireAtlasVaultInt(
        value['byte_count'],
        field: 'byte_count',
      );
      if (byteCount <= 0 ||
          byteCount > atlasVaultMaximumPairingArtifactByteCount) {
        throw const AtlasVaultPairingTransactionException();
      }
      return AtlasVaultStagedPairingArtifact._(
        kind: AtlasVaultPairingArtifactKind.values.singleWhere(
          (candidate) => candidate.encoded == kindText,
        ),
        sha256: _requiredSha256(value['sha256']),
        byteCount: byteCount,
      );
    } catch (_) {
      throw const AtlasVaultPairingTransactionException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'kind': kind.encoded,
    'sha256': sha256,
    'byte_count': byteCount,
  };
}

final class AtlasVaultPairingTransaction {
  AtlasVaultPairingTransaction._({
    required this.transactionId,
    required this.revision,
    required this.parentRevision,
    required this.role,
    required this.stage,
    required this.createdAt,
    required this.updatedAt,
    required this.installedAt,
    required this.localDeviceId,
    required this.peerDeviceId,
    required this.transcriptSha256,
    required this.offerSha256,
    required this.acceptanceSha256,
    required this.deliverySha256,
    required this.acknowledgementSha256,
    required this.bootstrapSha256,
    required this.vaultId,
    required this.keyEpoch,
    required Uint8List? ephemeralPrivateKey,
    required this.storeSha256,
    required this.vaultKeySha256,
    required this.selectionCommitted,
    required List<AtlasVaultStagedPairingArtifact> stagedArtifacts,
  }) : _ephemeralPrivateKey = ephemeralPrivateKey == null
           ? null
           : Uint8List.fromList(ephemeralPrivateKey),
       stagedArtifacts = List<AtlasVaultStagedPairingArtifact>.unmodifiable(
         stagedArtifacts,
       );

  final String transactionId;
  final String revision;
  final String? parentRevision;
  final AtlasVaultPairingRole role;
  final AtlasVaultPairingStage stage;
  final String createdAt;
  final String updatedAt;
  final String? installedAt;
  final String localDeviceId;
  final String? peerDeviceId;
  final String? transcriptSha256;
  final String? offerSha256;
  final String? acceptanceSha256;
  final String? deliverySha256;
  final String? acknowledgementSha256;
  final String? bootstrapSha256;
  final String? vaultId;
  final int? keyEpoch;
  final Uint8List? _ephemeralPrivateKey;
  final String? storeSha256;
  final String? vaultKeySha256;
  final bool selectionCommitted;
  final List<AtlasVaultStagedPairingArtifact> stagedArtifacts;

  Uint8List? get ephemeralPrivateKey => _ephemeralPrivateKey == null
      ? null
      : Uint8List.fromList(_ephemeralPrivateKey);

  factory AtlasVaultPairingTransaction.fromJson(Map<String, Object?> source) {
    Uint8List? ephemeralPrivateKey;
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'transaction_id',
          'revision',
          'parent_revision',
          'role',
          'stage',
          'created_at',
          'updated_at',
          'installed_at',
          'local_device_id',
          'peer_device_id',
          'transcript_sha256',
          'offer_sha256',
          'acceptance_sha256',
          'delivery_sha256',
          'acknowledgement_sha256',
          'bootstrap_sha256',
          'vault_id',
          'key_epoch',
          'ephemeral_private_key',
          'store_sha256',
          'vault_key_sha256',
          'selection_committed',
          'staged_artifacts',
        },
        context: 'Pairing transaction',
      );
      if (value['format'] != _transactionFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _transactionVersion) {
        throw const AtlasVaultPairingTransactionException();
      }
      final role = AtlasVaultPairingRole.values.singleWhere(
        (candidate) => candidate.encoded == value['role'],
      );
      final stage = AtlasVaultPairingStage.values.singleWhere(
        (candidate) => candidate.encoded == value['stage'],
      );
      final roleStages = _stagesFor(role);
      if (!roleStages.contains(stage)) {
        throw const AtlasVaultPairingTransactionException();
      }
      final createdAt = requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'created_at',
      );
      final updatedAt = requireAtlasVaultUtcSeconds(
        value['updated_at'],
        field: 'updated_at',
      );
      final installedAt = value['installed_at'] == null
          ? null
          : requireAtlasVaultUtcSeconds(
              value['installed_at'],
              field: 'installed_at',
            );
      if (_time(updatedAt).isBefore(_time(createdAt))) {
        throw const AtlasVaultPairingTransactionException();
      }
      final artifacts = <AtlasVaultStagedPairingArtifact>[
        for (final item in requireAtlasVaultList(
          value['staged_artifacts'],
          field: 'staged_artifacts',
        ))
          AtlasVaultStagedPairingArtifact.fromJson(
            requireAtlasVaultObject(item, context: 'Staged pairing artifact'),
          ),
      ];
      final artifactKinds = artifacts.map((item) => item.kind.index).toList();
      final sortedKinds = List<int>.from(artifactKinds)..sort();
      if (artifacts.length > AtlasVaultPairingArtifactKind.values.length ||
          artifactKinds.toSet().length != artifactKinds.length ||
          !_intListsEqual(artifactKinds, sortedKinds)) {
        throw const AtlasVaultPairingTransactionException();
      }
      requireAtlasVaultStagedPairingArtifactByteCounts(
        artifacts.map((artifact) => artifact.byteCount),
      );
      final encodedPrivateKey = value['ephemeral_private_key'];
      if (encodedPrivateKey != null) {
        ephemeralPrivateKey = requireAtlasVaultCanonicalBase64(
          encodedPrivateKey,
          field: 'ephemeral_private_key',
          exactLength: 32,
        );
      }
      final keyEpoch = _optionalPositiveInt(value['key_epoch']);
      final selectionCommitted = requireAtlasVaultBool(
        value['selection_committed'],
        field: 'selection_committed',
      );
      if (selectionCommitted && role != AtlasVaultPairingRole.invitee) {
        throw const AtlasVaultPairingTransactionException();
      }
      final runtimeActivated =
          role == AtlasVaultPairingRole.invitee &&
          roleStages.indexOf(stage) >=
              roleStages.indexOf(AtlasVaultPairingStage.runtimeActivated);
      if ((runtimeActivated && installedAt == null) ||
          (!runtimeActivated && installedAt != null) ||
          (installedAt != null &&
              (_time(installedAt).isBefore(_time(createdAt)) ||
                  _time(installedAt).isAfter(_time(updatedAt))))) {
        throw const AtlasVaultPairingTransactionException();
      }
      return AtlasVaultPairingTransaction._(
        transactionId: requireAtlasVaultCanonicalUuid(
          value['transaction_id'],
          field: 'transaction_id',
        ),
        revision: requireAtlasVaultCanonicalUuid(
          value['revision'],
          field: 'revision',
        ),
        parentRevision: _optionalUuid(value['parent_revision']),
        role: role,
        stage: stage,
        createdAt: createdAt,
        updatedAt: updatedAt,
        installedAt: installedAt,
        localDeviceId: _requiredDeviceId(value['local_device_id']),
        peerDeviceId: _optionalDeviceId(value['peer_device_id']),
        transcriptSha256: _optionalSha256(value['transcript_sha256']),
        offerSha256: _optionalSha256(value['offer_sha256']),
        acceptanceSha256: _optionalSha256(value['acceptance_sha256']),
        deliverySha256: _optionalSha256(value['delivery_sha256']),
        acknowledgementSha256: _optionalSha256(value['acknowledgement_sha256']),
        bootstrapSha256: _optionalSha256(value['bootstrap_sha256']),
        vaultId: value['vault_id'] == null
            ? null
            : requireAtlasVaultVaultId(value['vault_id']),
        keyEpoch: keyEpoch,
        ephemeralPrivateKey: ephemeralPrivateKey,
        storeSha256: _optionalSha256(value['store_sha256']),
        vaultKeySha256: _optionalSha256(value['vault_key_sha256']),
        selectionCommitted: selectionCommitted,
        stagedArtifacts: artifacts,
      );
    } catch (_) {
      throw const AtlasVaultPairingTransactionException();
    } finally {
      ephemeralPrivateKey?.fillRange(0, ephemeralPrivateKey.length, 0);
    }
  }

  factory AtlasVaultPairingTransaction.fromCanonicalBytes(Uint8List bytes) {
    Uint8List? input;
    Uint8List? canonical;
    try {
      requireAtlasVaultProtectedStateByteCount(
        AtlasVaultProtectedStateCategory.pairingTransactionJournal,
        bytes.length,
      );
      input = Uint8List.fromList(bytes);
      final decodedJson = jsonDecode(utf8.decode(input, allowMalformed: false));
      if (decodedJson is! Map) {
        throw const AtlasVaultPairingTransactionException();
      }
      final object = <String, Object?>{};
      for (final entry in decodedJson.entries) {
        if (entry.key is! String) {
          throw const AtlasVaultPairingTransactionException();
        }
        object[entry.key as String] = entry.value;
      }
      final decoded = AtlasVaultPairingTransaction.fromJson(object);
      canonical = decoded.canonicalBytes();
      if (!_bytesEqual(input, canonical)) {
        throw const AtlasVaultPairingTransactionException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultPairingTransactionException();
    } finally {
      input?.fillRange(0, input.length, 0);
      canonical?.fillRange(0, canonical.length, 0);
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _transactionFormat,
    'version': _transactionVersion,
    'transaction_id': transactionId,
    'revision': revision,
    'parent_revision': parentRevision,
    'role': role.encoded,
    'stage': stage.encoded,
    'created_at': createdAt,
    'updated_at': updatedAt,
    'installed_at': installedAt,
    'local_device_id': localDeviceId,
    'peer_device_id': peerDeviceId,
    'transcript_sha256': transcriptSha256,
    'offer_sha256': offerSha256,
    'acceptance_sha256': acceptanceSha256,
    'delivery_sha256': deliverySha256,
    'acknowledgement_sha256': acknowledgementSha256,
    'bootstrap_sha256': bootstrapSha256,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'ephemeral_private_key': _ephemeralPrivateKey == null
        ? null
        : base64Encode(_ephemeralPrivateKey),
    'store_sha256': storeSha256,
    'vault_key_sha256': vaultKeySha256,
    'selection_committed': selectionCommitted,
    'staged_artifacts': <Object?>[
      for (final artifact in stagedArtifacts) artifact.toJson(),
    ],
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  void destroy() {
    _ephemeralPrivateKey?.fillRange(0, _ephemeralPrivateKey.length, 0);
  }

  @override
  bool operator ==(Object other) =>
      other is AtlasVaultPairingTransaction &&
      _bytesEqual(canonicalBytes(), other.canonicalBytes());

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultPairingTransaction(<redacted>)';
}

void validateAtlasVaultPairingTransition(
  AtlasVaultPairingTransaction current,
  AtlasVaultPairingTransaction replacement,
) {
  try {
    final stages = _stagesFor(current.role);
    if (replacement.transactionId != current.transactionId ||
        replacement.role != current.role ||
        replacement.localDeviceId != current.localDeviceId ||
        replacement.createdAt != current.createdAt ||
        !_nullableTextCanAdvance(
          current.installedAt,
          replacement.installedAt,
        ) ||
        replacement.parentRevision != current.revision ||
        replacement.revision == current.revision ||
        _time(replacement.updatedAt).isBefore(_time(current.updatedAt)) ||
        stages.indexOf(replacement.stage) < stages.indexOf(current.stage) ||
        !_nullableTextCanAdvance(
          current.peerDeviceId,
          replacement.peerDeviceId,
        ) ||
        !_nullableTextCanAdvance(
          current.transcriptSha256,
          replacement.transcriptSha256,
        ) ||
        !_nullableTextCanAdvance(
          current.offerSha256,
          replacement.offerSha256,
        ) ||
        !_nullableTextCanAdvance(
          current.acceptanceSha256,
          replacement.acceptanceSha256,
        ) ||
        !_nullableTextCanAdvance(
          current.deliverySha256,
          replacement.deliverySha256,
        ) ||
        !_nullableTextCanAdvance(
          current.acknowledgementSha256,
          replacement.acknowledgementSha256,
        ) ||
        !_nullableTextCanAdvance(
          current.bootstrapSha256,
          replacement.bootstrapSha256,
        ) ||
        !_nullableTextCanAdvance(current.vaultId, replacement.vaultId) ||
        !_nullableIntCanAdvance(current.keyEpoch, replacement.keyEpoch) ||
        !_nullableTextCanAdvance(
          current.storeSha256,
          replacement.storeSha256,
        ) ||
        !_nullableTextCanAdvance(
          current.vaultKeySha256,
          replacement.vaultKeySha256,
        ) ||
        (current.selectionCommitted && !replacement.selectionCommitted)) {
      throw const AtlasVaultPairingTransactionException();
    }
  } catch (_) {
    throw const AtlasVaultPairingTransactionException();
  }
}

abstract interface class AtlasVaultTrustedDeviceRegistryStore {
  Future<AtlasVaultTrustedDeviceRegistry?> read();
  Future<void> create(AtlasVaultTrustedDeviceRegistry registry);
  Future<void> replace(
    AtlasVaultTrustedDeviceRegistry registry, {
    required String expectedSha256,
  });
}

abstract interface class AtlasVaultPairingReplayStateStore {
  Future<AtlasVaultPairingReplayStore?> read();
  Future<void> create(AtlasVaultPairingReplayStore replayStore);
  Future<void> replace(
    AtlasVaultPairingReplayStore replayStore, {
    required String expectedSha256,
  });
}

abstract interface class AtlasVaultPairingTransactionStore {
  Future<AtlasVaultPairingTransaction?> read();
  Future<void> create(AtlasVaultPairingTransaction transaction);
  Future<void> replace(
    AtlasVaultPairingTransaction transaction, {
    required String expectedSha256,
  });
  Future<void> delete({required String expectedSha256});
}

abstract interface class AtlasVaultPairingArtifactStageStore {
  Future<AtlasVaultPairingArtifact?> read(AtlasVaultPairingArtifactKind kind);
  Future<void> create(AtlasVaultPairingArtifact artifact);
  Future<void> delete(
    AtlasVaultPairingArtifactKind kind, {
    required String expectedSha256,
  });
}

abstract interface class AtlasVaultPairingArtifactTransport {
  Future<AtlasVaultPairingArtifact?> pick();
  Future<bool> save(AtlasVaultPairingArtifact artifact);
}

enum AtlasVaultPairingCleanInstallDisposition {
  clean,
  migrationRequired,
  existingVault,
  unavailable,
  recoveryRequired,
}

enum AtlasVaultTrustedPairingDisposition {
  ready,
  identityReady,
  offerReady,
  offerSaved,
  acceptanceReady,
  acceptanceSaved,
  codesReady,
  codesConfirmed,
  deliveryReady,
  deliverySaved,
  acknowledgementReady,
  acknowledgementSaved,
  completed,
  cancelled,
  migrationRequired,
  existingVault,
  unavailable,
  recoveryRequired,
  failed,
}

final class AtlasVaultTrustedPairingResult {
  const AtlasVaultTrustedPairingResult({
    required this.disposition,
    this.role,
    this.stage,
    this.localFingerprint,
    this.peerFingerprint,
    this.sas,
    this.expiresAt,
    this.trusted = false,
    this.pendingTransaction = false,
  });

  final AtlasVaultTrustedPairingDisposition disposition;
  final AtlasVaultPairingRole? role;
  final AtlasVaultPairingStage? stage;
  final String? localFingerprint;
  final String? peerFingerprint;
  final String? sas;
  final String? expiresAt;
  final bool trusted;
  final bool pendingTransaction;

  @override
  String toString() => 'AtlasVaultTrustedPairingResult(<redacted>)';
}

abstract interface class AtlasVaultTrustedPairingCoordinating {
  void cancelActiveOperation();

  Future<AtlasVaultTrustedPairingResult> inspect();

  Future<AtlasVaultTrustedPairingResult> createDeviceIdentity();

  Future<AtlasVaultTrustedPairingResult> createPairingOffer();

  Future<AtlasVaultTrustedPairingResult> savePairingOffer();

  Future<AtlasVaultTrustedPairingResult> importPairingOffer();

  Future<AtlasVaultTrustedPairingResult> savePairingAcceptance();

  Future<AtlasVaultTrustedPairingResult> importPairingAcceptance();

  Future<AtlasVaultTrustedPairingResult> confirmCodesMatch();

  Future<AtlasVaultTrustedPairingResult> saveKeyDelivery();

  Future<AtlasVaultTrustedPairingResult> importKeyDelivery();

  Future<AtlasVaultTrustedPairingResult> savePairingAcknowledgement();

  Future<AtlasVaultTrustedPairingResult> importPairingAcknowledgement();

  Future<AtlasVaultTrustedPairingResult> resumePairing();

  Future<AtlasVaultTrustedPairingResult> discardPairing();

  Future<void> stop();
}

abstract interface class AtlasVaultTrustedPairingTransactionAdmission {
  Future<T> runTrustedPairingTransaction<T>(Future<T> Function() operation);
}

final class AtlasVaultNoopTrustedPairingTransactionAdmission
    implements AtlasVaultTrustedPairingTransactionAdmission {
  const AtlasVaultNoopTrustedPairingTransactionAdmission();

  @override
  Future<T> runTrustedPairingTransaction<T>(Future<T> Function() operation) =>
      operation();
}

typedef AtlasVaultPairingIdentityGenerator =
    Future<AtlasVaultDeviceIdentity> Function();
typedef AtlasVaultPairingCleanInstallProbe =
    Future<AtlasVaultPairingCleanInstallDisposition> Function();
typedef AtlasVaultPairingUuidProvider = String Function();
typedef AtlasVaultPairingRandomBytes = Uint8List Function(int length);

final class AtlasVaultPairingMonotonicDeadline {
  AtlasVaultPairingMonotonicDeadline({
    required DateTime wallTime,
    required Duration monotonicTime,
  }) : _anchorWall = wallTime.toUtc(),
       _anchorMonotonic = monotonicTime,
       _lastMonotonic = monotonicTime;

  static const Duration _maximumLifetime = Duration(minutes: 10);

  final DateTime _anchorWall;
  final Duration _anchorMonotonic;
  Duration _lastMonotonic;
  DateTime? _expiresAt;
  Duration? _deadline;

  void present({
    required DateTime expiresAt,
    required DateTime currentTime,
    required Duration monotonicTime,
  }) {
    final (effective, monotonic) = _effectiveTime(
      currentTime: currentTime,
      monotonicTime: monotonicTime,
    );
    final expires = expiresAt.toUtc();
    var remaining = expires.difference(effective);
    if (remaining <= Duration.zero) {
      throw const AtlasVaultPairingException();
    }
    if (remaining > _maximumLifetime) remaining = _maximumLifetime;
    _expiresAt = expires;
    _deadline = monotonic + remaining;
  }

  void requireLive({
    required DateTime currentTime,
    required Duration monotonicTime,
  }) {
    final expiresAt = _expiresAt;
    final deadline = _deadline;
    if (expiresAt == null || deadline == null) {
      throw const AtlasVaultPairingException();
    }
    final (effective, monotonic) = _effectiveTime(
      currentTime: currentTime,
      monotonicTime: monotonicTime,
    );
    if (!effective.isBefore(expiresAt) || monotonic >= deadline) {
      throw const AtlasVaultPairingException();
    }
  }

  void clear() {
    _expiresAt = null;
    _deadline = null;
  }

  (DateTime, Duration) _effectiveTime({
    required DateTime currentTime,
    required Duration monotonicTime,
  }) {
    if (monotonicTime < _anchorMonotonic || monotonicTime < _lastMonotonic) {
      throw const AtlasVaultPairingException();
    }
    final monotonicWall = _anchorWall.add(monotonicTime - _anchorMonotonic);
    final wall = currentTime.toUtc();
    _lastMonotonic = monotonicTime;
    return (wall.isAfter(monotonicWall) ? wall : monotonicWall, monotonicTime);
  }
}

final class AtlasVaultTrustedPairingCoordinator
    implements AtlasVaultTrustedPairingCoordinating {
  AtlasVaultTrustedPairingCoordinator({
    required AtlasDeviceIdentitySecretStore identityStore,
    required AtlasVaultTrustedDeviceRegistryStore registryStore,
    required AtlasVaultPairingReplayStateStore replayStore,
    required AtlasVaultPairingTransactionStore transactionStore,
    required AtlasVaultPairingArtifactStageStore stageStore,
    required AtlasVaultPairingArtifactTransport artifactTransport,
    required AtlasVaultPrivateStateRuntime runtime,
    required AtlasVaultPairingCleanInstallProbe cleanInstallProbe,
    AtlasVaultMigrationSecureKeyStore? secureKeyStore,
    AtlasVaultLocalStoreIO? localStoreIO,
    AtlasVaultSelectedVaultStore? selectedVaultStore,
    Future<bool> Function(String vaultId)? activateInstalledVault,
    AtlasVaultTrustedPairingTransactionAdmission? transactionAdmission,
    AtlasVaultPairingIdentityGenerator? identityGenerator,
    AtlasVaultPairingUuidProvider? uuidProvider,
    AtlasVaultPairingRandomBytes? randomBytes,
    DateTime Function()? now,
    Duration Function()? monotonicNow,
    required AtlasVaultPairingKeyReleaseAuthorization authorizeKeyRelease,
  }) : // Keep public dependency labels explicit at composition sites.
       // ignore: prefer_initializing_formals
       _identityStore = identityStore,
       // ignore: prefer_initializing_formals
       _registryStore = registryStore,
       // ignore: prefer_initializing_formals
       _replayStore = replayStore,
       // ignore: prefer_initializing_formals
       _transactionStore = transactionStore,
       // ignore: prefer_initializing_formals
       _stageStore = stageStore,
       // ignore: prefer_initializing_formals
       _artifactTransport = artifactTransport,
       _runtime = runtime,
       // ignore: prefer_initializing_formals
       _cleanInstallProbe = cleanInstallProbe,
       // ignore: prefer_initializing_formals
       _secureKeyStore = secureKeyStore,
       // ignore: prefer_initializing_formals
       _localStoreIO = localStoreIO,
       // ignore: prefer_initializing_formals
       _selectedVaultStore = selectedVaultStore,
       _activateInstalledVault =
           activateInstalledVault ??
           ((vaultId) async =>
               await runtime.activateExisting(vaultId) ==
               AtlasVaultActivationResult.activated),
       _transactionAdmission =
           transactionAdmission ??
           const AtlasVaultNoopTrustedPairingTransactionAdmission(),
       _identityGenerator =
           identityGenerator ?? (() => generateAtlasVaultDeviceIdentity()),
       _uuidProvider = uuidProvider ?? _securePairingUuid,
       _randomBytes = randomBytes ?? _securePairingBytes,
       _now = now ?? DateTime.now,
       _monotonicNow = monotonicNow ?? _pairingMonotonicElapsed,
       // ignore: prefer_initializing_formals
       _authorizeKeyRelease = authorizeKeyRelease;

  final AtlasDeviceIdentitySecretStore _identityStore;
  final AtlasVaultTrustedDeviceRegistryStore _registryStore;
  final AtlasVaultPairingReplayStateStore _replayStore;
  final AtlasVaultPairingTransactionStore _transactionStore;
  final AtlasVaultPairingArtifactStageStore _stageStore;
  final AtlasVaultPairingArtifactTransport _artifactTransport;
  final AtlasVaultPrivateStateRuntime _runtime;
  final AtlasVaultPairingCleanInstallProbe _cleanInstallProbe;
  final AtlasVaultMigrationSecureKeyStore? _secureKeyStore;
  final AtlasVaultLocalStoreIO? _localStoreIO;
  final AtlasVaultSelectedVaultStore? _selectedVaultStore;
  final Future<bool> Function(String vaultId) _activateInstalledVault;
  final AtlasVaultTrustedPairingTransactionAdmission _transactionAdmission;
  final AtlasVaultPairingIdentityGenerator _identityGenerator;
  final AtlasVaultPairingUuidProvider _uuidProvider;
  final AtlasVaultPairingRandomBytes _randomBytes;
  final DateTime Function() _now;
  final Duration Function() _monotonicNow;
  final AtlasVaultPairingKeyReleaseAuthorization _authorizeKeyRelease;
  AtlasVaultPairingMonotonicDeadline? _pairingDeadline;

  Future<void>? _operation;
  bool _stopped = false;
  final Object _operationLeaseKey = Object();
  int _operationGeneration = 0;

  @override
  Future<AtlasVaultTrustedPairingResult> inspect() => _run(() async {
    final transaction = await _transactionStore.read();
    final identity = await _loadIdentity();
    try {
      if (transaction == null) {
        return AtlasVaultTrustedPairingResult(
          disposition: identity == null
              ? AtlasVaultTrustedPairingDisposition.ready
              : AtlasVaultTrustedPairingDisposition.identityReady,
          localFingerprint: identity == null
              ? null
              : atlasVaultPairingDeviceFingerprint(identity.deviceId),
        );
      }
      if (_inspectRequiresLivePairingDeadline(transaction.stage)) {
        await _requireLivePairingDeadlineFor(transaction);
      }
      return await _resultFor(transaction, identity: identity);
    } finally {
      identity?.destroy();
      transaction?.destroy();
    }
  });

  @override
  Future<AtlasVaultTrustedPairingResult> createDeviceIdentity() =>
      _run(() async {
        AtlasVaultDeviceIdentity? identity = await _loadIdentity();
        try {
          if (identity == null) {
            final custody = AtlasDeviceIdentityCustody(
              _identityStore,
              identityGenerator: _identityGenerator,
            );
            _authorizeSensitiveMutation();
            await custody.createPrimaryIdentity();
            identity = await _loadIdentity();
          }
          if (identity == null) {
            throw const AtlasVaultPairingStorageException();
          }
          return AtlasVaultTrustedPairingResult(
            disposition: AtlasVaultTrustedPairingDisposition.identityReady,
            localFingerprint: atlasVaultPairingDeviceFingerprint(
              identity.deviceId,
            ),
          );
        } catch (_) {
          return _fixed(AtlasVaultTrustedPairingDisposition.failed);
        } finally {
          identity?.destroy();
        }
      });

  @override
  Future<AtlasVaultTrustedPairingResult> createPairingOffer() => _run(
    () => _transactionAdmission.runTrustedPairingTransaction(() async {
      AtlasVaultDeviceIdentity? identity;
      try {
        if (await _transactionStore.read() != null) {
          return _fixed(
            AtlasVaultTrustedPairingDisposition.recoveryRequired,
            pending: true,
          );
        }
        identity = await _requireIdentity();
        return await _runtime.withInteroperabilitySession((session) async {
          final issued = _now().toUtc();
          final issuedAt = _utc(issued);
          final expires = issued.add(const Duration(minutes: 10));
          final expiresAt = _utc(expires);
          _presentPairingDeadline(expiresAt: expires, currentTime: issued);
          final signed = await createAtlasVaultPairingOffer(
            inviter: identity!,
            offerId: _uuidProvider(),
            nonce: _randomBytes(32),
            issuedAt: issuedAt,
            expiresAt: expiresAt,
          );
          final artifact = _artifact(
            AtlasVaultPairingArtifactKind.offer,
            <String, Object?>{'signed_offer': signed.toJson()},
          );
          final transaction = await _newTransaction(
            role: AtlasVaultPairingRole.inviter,
            stage: AtlasVaultPairingStage.offerCreated,
            localDeviceId: identity.deviceId,
            createdAt: issuedAt,
            offerSha256: await atlasVaultSha256Hex(artifact.canonicalBytes()),
            vaultId: session.vaultId,
            keyEpoch: _initialVaultKeyEpoch,
            stagedArtifacts: <AtlasVaultPairingArtifact>[artifact],
          );
          _authorizeSensitiveMutation();
          await _transactionStore.create(transaction);
          await _createStaged(artifact);
          await _requireTransaction(transaction);
          return _result(
            AtlasVaultTrustedPairingDisposition.offerReady,
            transaction,
            local: identity,
            expiresAt: expiresAt,
          );
        });
      } catch (_) {
        return _fixed(AtlasVaultTrustedPairingDisposition.unavailable);
      } finally {
        identity?.destroy();
      }
    }),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingOffer() => _run(
    () => _saveArtifact(
      expectedRole: AtlasVaultPairingRole.inviter,
      expectedStage: AtlasVaultPairingStage.offerCreated,
      kind: AtlasVaultPairingArtifactKind.offer,
      savedStage: AtlasVaultPairingStage.offerSaved,
      disposition: AtlasVaultTrustedPairingDisposition.offerSaved,
    ),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingOffer() => _run(
    () => _transactionAdmission.runTrustedPairingTransaction(() async {
      AtlasVaultDeviceIdentity? identity;
      Uint8List? ephemeralPrivate;
      try {
        final clean = await _cleanInstallProbe();
        final blocked = _cleanInstallResult(clean);
        if (blocked != null) return blocked;
        if (await _transactionStore.read() != null) {
          return _fixed(
            AtlasVaultTrustedPairingDisposition.recoveryRequired,
            pending: true,
          );
        }
        identity = await _requireIdentity();
        final offerArtifact = await _artifactTransport.pick();
        if (offerArtifact == null) {
          return _fixed(AtlasVaultTrustedPairingDisposition.cancelled);
        }
        if (offerArtifact.kind != AtlasVaultPairingArtifactKind.offer) {
          throw const AtlasVaultPairingTransactionException();
        }
        final signedOffer = _signedOffer(offerArtifact);
        final currentTime = _utc(_now());
        final offer = await verifyAtlasVaultPairingOffer(
          signedOffer,
          currentTime: currentTime,
        );
        _presentPairingDeadline(
          expiresAt: _time(offer.expiresAt),
          currentTime: _time(currentTime),
        );
        if (offer.inviter.descriptor.deviceId == identity.deviceId) {
          throw const AtlasVaultPairingException();
        }
        await _requireRegistryAdmission(
          localDeviceId: identity.deviceId,
          peerDeviceId: offer.inviter.descriptor.deviceId,
        );
        final acceptance = await createAtlasVaultPairingAcceptance(
          invitee: identity,
          signedOffer: signedOffer,
          nonce: _randomBytes(32),
          acceptedAt: currentTime,
          currentTime: currentTime,
        );
        final transcript = await atlasVaultPairingTranscriptSha256(
          signedOffer,
          acceptance,
        );
        ephemeralPrivate = _randomBytes(32);
        final ephemeralPair = await X25519().newKeyPairFromSeed(
          ephemeralPrivate,
        );
        final ephemeralPublic = await ephemeralPair.extractPublicKey();
        final request = await createAtlasVaultPairingKeyRequest(
          invitee: identity,
          requestId: _uuidProvider(),
          transcriptSha256: transcript,
          inviterDeviceId: offer.inviter.descriptor.deviceId,
          inviteeEphemeralPublicKey: Uint8List.fromList(ephemeralPublic.bytes),
          nonce: _randomBytes(32),
          issuedAt: currentTime,
          expiresAt: offer.expiresAt,
        );
        final sessionKey = await deriveAtlasVaultPairingSessionKey(
          localIdentity: identity,
          signedOffer: signedOffer,
          signedAcceptance: acceptance,
        );
        final proofs = await deriveAtlasVaultPairingProofs(
          sessionKey: sessionKey,
          transcriptSha256: transcript,
        );
        final sas = await deriveAtlasVaultPairingSas(sessionKey, transcript);
        sessionKey.fillRange(0, sessionKey.length, 0);
        final acceptanceArtifact = _artifact(
          AtlasVaultPairingArtifactKind.acceptance,
          <String, Object?>{
            'signed_acceptance': acceptance.toJson(),
            'signed_key_request': request.toJson(),
            'invitee_proof': base64Encode(proofs.invitee),
          },
        );
        final transaction = await _newTransaction(
          role: AtlasVaultPairingRole.invitee,
          stage: AtlasVaultPairingStage.acceptanceCreated,
          localDeviceId: identity.deviceId,
          createdAt: currentTime,
          peerDeviceId: offer.inviter.descriptor.deviceId,
          transcriptSha256: _hexBytes(transcript),
          offerSha256: await atlasVaultSha256Hex(
            offerArtifact.canonicalBytes(),
          ),
          acceptanceSha256: await atlasVaultSha256Hex(
            acceptanceArtifact.canonicalBytes(),
          ),
          ephemeralPrivateKey: ephemeralPrivate,
          stagedArtifacts: <AtlasVaultPairingArtifact>[
            offerArtifact,
            acceptanceArtifact,
          ],
        );
        _authorizeSensitiveMutation();
        await _transactionStore.create(transaction);
        await _createStaged(offerArtifact);
        await _createStaged(acceptanceArtifact);
        await _requireTransaction(transaction);
        return _result(
          AtlasVaultTrustedPairingDisposition.acceptanceReady,
          transaction,
          local: identity,
          peerDeviceId: offer.inviter.descriptor.deviceId,
          sas: sas,
          expiresAt: offer.expiresAt,
        );
      } catch (_) {
        return _fixed(AtlasVaultTrustedPairingDisposition.recoveryRequired);
      } finally {
        _wipeBytes(ephemeralPrivate);
        identity?.destroy();
      }
    }),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingAcceptance() => _run(
    () => _saveArtifact(
      expectedRole: AtlasVaultPairingRole.invitee,
      expectedStage: AtlasVaultPairingStage.acceptanceCreated,
      kind: AtlasVaultPairingArtifactKind.acceptance,
      savedStage: AtlasVaultPairingStage.acceptanceSaved,
      disposition: AtlasVaultTrustedPairingDisposition.acceptanceSaved,
    ),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingAcceptance() => _run(
    () async {
      AtlasVaultDeviceIdentity? identity;
      AtlasVaultPairingSession? session;
      try {
        final transaction = await _requireStage(
          AtlasVaultPairingRole.inviter,
          AtlasVaultPairingStage.offerSaved,
        );
        identity = await _requireIdentity();
        final offerArtifact = await _requireStaged(
          AtlasVaultPairingArtifactKind.offer,
          transaction,
        );
        final acceptanceArtifact = await _artifactTransport.pick();
        if (acceptanceArtifact == null) {
          return _fixed(AtlasVaultTrustedPairingDisposition.cancelled);
        }
        if (acceptanceArtifact.kind !=
            AtlasVaultPairingArtifactKind.acceptance) {
          throw const AtlasVaultPairingTransactionException();
        }
        final signedOffer = _signedOffer(offerArtifact);
        _requireLivePairingDeadline(
          expiresAt: _time(signedOffer.offer.expiresAt),
        );
        final signedAcceptance = _signedAcceptance(acceptanceArtifact);
        final keyRequest = _keyRequest(acceptanceArtifact);
        final transcript = await atlasVaultPairingTranscriptSha256(
          signedOffer,
          signedAcceptance,
        );
        await verifyAtlasVaultPairingKeyRequest(
          keyRequest,
          transcriptSha256: transcript,
          inviterDeviceId: identity.deviceId,
          inviteeDeviceId:
              signedAcceptance.acceptance.invitee.descriptor.deviceId,
          currentTime: _utc(_now()),
        );
        final sessionKey = await deriveAtlasVaultPairingSessionKey(
          localIdentity: identity,
          signedOffer: signedOffer,
          signedAcceptance: signedAcceptance,
        );
        final proofs = await deriveAtlasVaultPairingProofs(
          sessionKey: sessionKey,
          transcriptSha256: transcript,
        );
        final suppliedInvitee = _proof(acceptanceArtifact, 'invitee_proof');
        session = await verifyAtlasVaultPairingTranscript(
          localIdentity: identity,
          signedOffer: signedOffer,
          signedAcceptance: signedAcceptance,
          proofs: AtlasVaultPairingProofs(
            inviter: proofs.inviter,
            invitee: suppliedInvitee,
          ),
          currentTime: _utc(_now()),
          replayGuard: const _AcceptingPairingReplayGuard(),
        );
        sessionKey.fillRange(0, sessionKey.length, 0);
        suppliedInvitee.fillRange(0, suppliedInvitee.length, 0);
        await _requireRegistryAdmission(
          localDeviceId: identity.deviceId,
          peerDeviceId: signedAcceptance.acceptance.invitee.descriptor.deviceId,
        );
        final intent = await _advance(
          transaction,
          transaction.stage,
          <String, Object?>{
            'peer_device_id':
                signedAcceptance.acceptance.invitee.descriptor.deviceId,
            'transcript_sha256': _hexBytes(transcript),
            'acceptance_sha256': await atlasVaultSha256Hex(
              acceptanceArtifact.canonicalBytes(),
            ),
            'staged_artifacts': await _stagedJson(<AtlasVaultPairingArtifact>[
              offerArtifact,
              acceptanceArtifact,
            ]),
          },
        );
        await _createStaged(acceptanceArtifact);
        final updated = await _advance(
          intent,
          AtlasVaultPairingStage.acceptanceImported,
        );
        return _result(
          AtlasVaultTrustedPairingDisposition.codesReady,
          updated,
          local: identity,
          peerDeviceId: signedAcceptance.acceptance.invitee.descriptor.deviceId,
          sas: await deriveAtlasVaultPairingSas(
            session.sessionKey,
            session.transcriptSha256,
          ),
          expiresAt: signedOffer.offer.expiresAt,
        );
      } catch (_) {
        return _fixed(AtlasVaultTrustedPairingDisposition.recoveryRequired);
      } finally {
        session?.destroy();
        identity?.destroy();
      }
    },
  );

  @override
  Future<AtlasVaultTrustedPairingResult> confirmCodesMatch() => _run(() async {
    AtlasVaultPairingTransaction? transaction;
    AtlasVaultDeviceIdentity? identity;
    try {
      transaction = await _transactionStore.read();
      if (transaction == null) {
        return _fixed(AtlasVaultTrustedPairingDisposition.failed);
      }
      identity = await _requireIdentity();
      if (transaction.role == AtlasVaultPairingRole.invitee) {
        if (transaction.stage != AtlasVaultPairingStage.acceptanceSaved) {
          return _fixed(AtlasVaultTrustedPairingDisposition.failed);
        }
        await _requireLivePairingDeadlineFor(transaction);
        final confirmed = await _advance(
          transaction,
          AtlasVaultPairingStage.sasConfirmed,
        );
        return await _completeInviteeSasConfirmation(
          confirmed,
          identity,
          acceptExactReplay: false,
        );
      }
      if (transaction.stage != AtlasVaultPairingStage.acceptanceImported) {
        return _fixed(AtlasVaultTrustedPairingDisposition.failed);
      }
      await _requireLivePairingDeadlineFor(transaction);
      return await _runtime.withInteroperabilitySession((vaultSession) async {
        Uint8List? vaultKey;
        Uint8List? inviterEphemeral;
        try {
          final current = transaction!;
          final acceptanceArtifact = await _requireStaged(
            AtlasVaultPairingArtifactKind.acceptance,
            current,
          );
          final keyRequest = _keyRequest(acceptanceArtifact);
          final transcript = _requiredHex(current.transcriptSha256);
          final currentStore = await vaultSession.readCurrentLocalStore();
          final bootstrap = AtlasVaultPairingBootstrap.fromJson(
            <String, Object?>{
              'format': 'atlasvault-pairing-bootstrap',
              'version': 1,
              'snapshot_id': _uuidProvider(),
              'created_at': _utc(_now()),
              'vault_metadata': currentStore.vaultMetadata.toJson(),
              'records': <Object?>[
                for (final record in currentStore.records) record.toJson(),
              ],
            },
          );
          await verifyAtlasVaultPairingKeyRequest(
            keyRequest,
            transcriptSha256: transcript,
            inviterDeviceId: identity!.deviceId,
            inviteeDeviceId: current.peerDeviceId!,
            currentTime: _utc(_now()),
          );
          if (!await _authorizeKeyRelease(vaultSession.vaultId)) {
            throw const AtlasVaultPairingTransactionException();
          }
          await _requireLivePairingDeadlineFor(current);
          await verifyAtlasVaultPairingKeyRequest(
            keyRequest,
            transcriptSha256: transcript,
            inviterDeviceId: identity.deviceId,
            inviteeDeviceId: current.peerDeviceId!,
            currentTime: _utc(_now()),
          );
          _authorizeSensitiveMutation();
          vaultKey = vaultSession.copyVaultKey();
          final confirmed = await _advance(
            current,
            AtlasVaultPairingStage.sasConfirmed,
          );
          inviterEphemeral = _randomBytes(32);
          final delivery = await createAtlasVaultKeyDelivery(
            inviter: identity,
            keyRequest: keyRequest,
            transcriptSha256: transcript,
            bootstrap: bootstrap,
            vaultKey: vaultKey,
            inviterEphemeralPrivateKey: inviterEphemeral,
            nonce: _randomBytes(12),
            deliveryId: _uuidProvider(),
            keyEpoch: confirmed.keyEpoch ?? _initialVaultKeyEpoch,
            expiresAt: keyRequest.request.expiresAt,
          );
          final sessionKey = await _sessionKeyFor(confirmed, identity);
          final proofs = await deriveAtlasVaultPairingProofs(
            sessionKey: sessionKey,
            transcriptSha256: transcript,
          );
          sessionKey.fillRange(0, sessionKey.length, 0);
          final deliveryArtifact = _artifact(
            AtlasVaultPairingArtifactKind.delivery,
            <String, Object?>{
              'signed_delivery': delivery.toJson(),
              'bootstrap': bootstrap.toJson(),
              'inviter_proof': base64Encode(proofs.inviter),
            },
          );
          final intent =
              await _advance(confirmed, confirmed.stage, <String, Object?>{
                'delivery_sha256': await atlasVaultSha256Hex(
                  deliveryArtifact.canonicalBytes(),
                ),
                'bootstrap_sha256': await atlasVaultSha256Hex(
                  bootstrap.canonicalBytes(),
                ),
                'vault_id': vaultSession.vaultId,
                'staged_artifacts': await _mergedStagedJson(
                  confirmed,
                  deliveryArtifact,
                ),
              });
          await _createStaged(deliveryArtifact);
          final created = await _advance(
            intent,
            AtlasVaultPairingStage.deliveryCreated,
          );
          return _result(
            AtlasVaultTrustedPairingDisposition.deliveryReady,
            created,
            local: identity,
            peerDeviceId: created.peerDeviceId,
            sas: await _sasFor(created, identity),
            expiresAt: keyRequest.request.expiresAt,
          );
        } finally {
          _wipeBytes(vaultKey);
          _wipeBytes(inviterEphemeral);
        }
      });
    } catch (_) {
      return _fixed(AtlasVaultTrustedPairingDisposition.recoveryRequired);
    } finally {
      identity?.destroy();
      transaction?.destroy();
    }
  });

  @override
  Future<AtlasVaultTrustedPairingResult> saveKeyDelivery() => _run(
    () => _saveArtifact(
      expectedRole: AtlasVaultPairingRole.inviter,
      expectedStage: AtlasVaultPairingStage.deliveryCreated,
      sideEffectIntentStage: AtlasVaultPairingStage.deliveryExportStarted,
      kind: AtlasVaultPairingArtifactKind.delivery,
      savedStage: AtlasVaultPairingStage.deliverySaved,
      disposition: AtlasVaultTrustedPairingDisposition.deliverySaved,
    ),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> importKeyDelivery() => _run(
    () => _transactionAdmission.runTrustedPairingTransaction(() async {
      try {
        final transaction = await _requireStage(
          AtlasVaultPairingRole.invitee,
          AtlasVaultPairingStage.offerConsumed,
        );
        final artifact = await _artifactTransport.pick();
        if (artifact == null) {
          return _fixed(AtlasVaultTrustedPairingDisposition.cancelled);
        }
        if (artifact.kind != AtlasVaultPairingArtifactKind.delivery) {
          throw const AtlasVaultPairingTransactionException();
        }
        final artifactHash = await atlasVaultSha256Hex(
          artifact.canonicalBytes(),
        );
        if (transaction.deliverySha256 != null &&
            transaction.deliverySha256 != artifactHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        await _preflightInviteeDelivery(transaction, artifact);
        final intent = await _advance(
          transaction,
          transaction.stage,
          <String, Object?>{
            'delivery_sha256': artifactHash,
            'bootstrap_sha256': await atlasVaultSha256Hex(
              _bootstrap(artifact).canonicalBytes(),
            ),
            'vault_id': _delivery(artifact).delivery.vaultId,
            'key_epoch': _delivery(artifact).delivery.keyEpoch,
            'staged_artifacts': await _mergedStagedJson(transaction, artifact),
          },
        );
        await _createStaged(artifact);
        final imported = await _advance(
          intent,
          AtlasVaultPairingStage.deliveryImported,
        );
        return await _installInvitee(imported);
      } catch (_) {
        return _fixed(
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
          pending: true,
        );
      }
    }),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> savePairingAcknowledgement() =>
      _run(() async {
        final result = await _saveArtifact(
          expectedRole: AtlasVaultPairingRole.invitee,
          expectedStage: AtlasVaultPairingStage.acknowledgementCreated,
          kind: AtlasVaultPairingArtifactKind.acknowledgement,
          savedStage: AtlasVaultPairingStage.acknowledgementSaved,
          disposition: AtlasVaultTrustedPairingDisposition.acknowledgementSaved,
        );
        if (result.disposition !=
            AtlasVaultTrustedPairingDisposition.acknowledgementSaved) {
          return result;
        }
        final transaction = await _transactionStore.read();
        if (transaction == null) {
          return _fixed(AtlasVaultTrustedPairingDisposition.recoveryRequired);
        }
        await _clearTransaction(transaction);
        return AtlasVaultTrustedPairingResult(
          disposition: AtlasVaultTrustedPairingDisposition.completed,
          role: AtlasVaultPairingRole.invitee,
          trusted: true,
        );
      });

  @override
  Future<AtlasVaultTrustedPairingResult> importPairingAcknowledgement() =>
      _run(() async {
        AtlasVaultDeviceIdentity? identity;
        try {
          final transaction = await _requireStage(
            AtlasVaultPairingRole.inviter,
            AtlasVaultPairingStage.deliverySaved,
          );
          identity = await _requireIdentity();
          final artifact = await _artifactTransport.pick();
          if (artifact == null) {
            return _fixed(AtlasVaultTrustedPairingDisposition.cancelled);
          }
          if (artifact.kind != AtlasVaultPairingArtifactKind.acknowledgement) {
            throw const AtlasVaultPairingTransactionException();
          }
          await _verifyInviterAcknowledgement(
            transaction,
            identity,
            acknowledgementArtifact: artifact,
          );
          final intent =
              await _advance(transaction, transaction.stage, <String, Object?>{
                'acknowledgement_sha256': await atlasVaultSha256Hex(
                  artifact.canonicalBytes(),
                ),
                'staged_artifacts': await _mergedStagedJson(
                  transaction,
                  artifact,
                ),
              });
          await _createStaged(artifact);
          final imported = await _advance(
            intent,
            AtlasVaultPairingStage.acknowledgementImported,
          );
          return _completeInviterAcknowledgement(imported, identity);
        } catch (_) {
          return _fixed(
            AtlasVaultTrustedPairingDisposition.recoveryRequired,
            pending: true,
          );
        } finally {
          identity?.destroy();
        }
      });

  @override
  Future<AtlasVaultTrustedPairingResult> resumePairing() => _run(
    () => _transactionAdmission.runTrustedPairingTransaction(() async {
      final transaction = await _transactionStore.read();
      if (transaction == null) {
        return _fixed(AtlasVaultTrustedPairingDisposition.ready);
      }
      try {
        if (transaction.role == AtlasVaultPairingRole.invitee &&
            transaction.stage == AtlasVaultPairingStage.acknowledgementSaved) {
          await _clearTransaction(transaction);
          return const AtlasVaultTrustedPairingResult(
            disposition: AtlasVaultTrustedPairingDisposition.completed,
            role: AtlasVaultPairingRole.invitee,
            trusted: true,
          );
        }
        if (transaction.role == AtlasVaultPairingRole.invitee &&
            transaction.stage == AtlasVaultPairingStage.sasConfirmed) {
          final identity = await _requireIdentity();
          try {
            return await _completeInviteeSasConfirmation(
              transaction,
              identity,
              acceptExactReplay: true,
            );
          } finally {
            identity.destroy();
          }
        }
        if (transaction.role == AtlasVaultPairingRole.invitee &&
            _stageAtLeast(
              transaction,
              AtlasVaultPairingStage.deliveryImported,
            ) &&
            transaction.stage != AtlasVaultPairingStage.acknowledgementSaved) {
          return await _installInvitee(transaction);
        }
        if (transaction.role == AtlasVaultPairingRole.inviter &&
            transaction.stage == AtlasVaultPairingStage.trustCommitted) {
          await _clearTransaction(transaction);
          return AtlasVaultTrustedPairingResult(
            disposition: AtlasVaultTrustedPairingDisposition.completed,
            role: AtlasVaultPairingRole.inviter,
            localFingerprint: atlasVaultPairingDeviceFingerprint(
              transaction.localDeviceId,
            ),
            peerFingerprint: transaction.peerDeviceId == null
                ? null
                : atlasVaultPairingDeviceFingerprint(transaction.peerDeviceId!),
            trusted: true,
          );
        }
        if (transaction.role == AtlasVaultPairingRole.inviter &&
            _stageAtLeast(
              transaction,
              AtlasVaultPairingStage.acknowledgementImported,
            )) {
          final identity = await _requireIdentity();
          try {
            return await _completeInviterAcknowledgement(transaction, identity);
          } finally {
            identity.destroy();
          }
        }
        if (transaction.role == AtlasVaultPairingRole.inviter &&
            transaction.stage == AtlasVaultPairingStage.sasConfirmed) {
          final identity = await _requireIdentity();
          try {
            final recovered = await _resumeGeneratedDeliveryOrphan(
              transaction,
              identity,
            );
            if (recovered != null) return recovered;
          } finally {
            identity.destroy();
          }
        }
        return await _resultFor(transaction);
      } catch (_) {
        return _fixed(
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
          pending: true,
        );
      } finally {
        transaction.destroy();
      }
    }),
  );

  @override
  Future<AtlasVaultTrustedPairingResult> discardPairing() => _run(
    () => _transactionAdmission.runTrustedPairingTransaction(() async {
      AtlasVaultPairingTransaction? transaction;
      Uint8List? key;
      try {
        transaction = await _transactionStore.read();
        if (transaction == null) {
          return _fixed(AtlasVaultTrustedPairingDisposition.ready);
        }
        final inviterPastBoundary =
            transaction.role == AtlasVaultPairingRole.inviter &&
            _stageAtLeast(
              transaction,
              AtlasVaultPairingStage.deliveryExportStarted,
            );
        final inviteePastBoundary =
            transaction.role == AtlasVaultPairingRole.invitee &&
            (transaction.selectionCommitted ||
                _stageAtLeast(
                  transaction,
                  AtlasVaultPairingStage.selectionCommitted,
                ));
        if (inviterPastBoundary || inviteePastBoundary) {
          return _fixed(
            AtlasVaultTrustedPairingDisposition.recoveryRequired,
            pending: true,
          );
        }
        if (transaction.role == AtlasVaultPairingRole.invitee) {
          final selected = _requireInstallDependency(_selectedVaultStore);
          if (await selected.read() != null) {
            return _fixed(
              AtlasVaultTrustedPairingDisposition.recoveryRequired,
              pending: true,
            );
          }
        }
        final vaultId = transaction.vaultId;
        if (vaultId != null && transaction.storeSha256 != null) {
          final localStore = _requireInstallDependency(_localStoreIO);
          final store = await localStore.read(vaultId);
          if (store != null &&
              await atlasVaultSha256Hex(store.canonicalBytes()) !=
                  transaction.storeSha256) {
            throw const AtlasVaultPairingTransactionException();
          }
          if (store != null) {
            _authorizeSensitiveMutation();
            await localStore.delete(vaultId);
          }
          if (await localStore.read(vaultId) != null) {
            throw const AtlasVaultPairingTransactionException();
          }
        }
        if (vaultId != null && transaction.vaultKeySha256 != null) {
          final secure = _requireInstallDependency(_secureKeyStore);
          key = await secure.loadVaultKey(vaultId);
          if (key != null &&
              await atlasVaultSha256Hex(key) != transaction.vaultKeySha256) {
            throw const AtlasVaultPairingTransactionException();
          }
          if (key != null) {
            _authorizeSensitiveMutation();
            await secure.deleteVaultKey(vaultId);
          }
          if (await secure.containsVaultKey(vaultId)) {
            throw const AtlasVaultPairingTransactionException();
          }
        }
        await _clearTransaction(transaction);
        return _fixed(AtlasVaultTrustedPairingDisposition.identityReady);
      } catch (_) {
        return _fixed(
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
          pending: true,
        );
      } finally {
        _wipeBytes(key);
        transaction?.destroy();
      }
    }),
  );

  @override
  void cancelActiveOperation() {
    _operationGeneration += 1;
  }

  @override
  Future<void> stop() async {
    _stopped = true;
    cancelActiveOperation();
    await _operation;
  }

  Future<AtlasVaultTrustedPairingResult> _installInvitee(
    AtlasVaultPairingTransaction starting,
  ) async {
    AtlasVaultDeviceIdentity? identity;
    Uint8List? vaultKey;
    Uint8List? loadedKey;
    try {
      identity = await _requireIdentity();
      final clean = await _cleanInstallProbe();
      if (!_stageAtLeast(starting, AtlasVaultPairingStage.storeCreated) &&
          starting.storeSha256 == null) {
        final blocked = _cleanInstallResult(clean);
        if (blocked != null) return blocked;
      } else if (clean ==
              AtlasVaultPairingCleanInstallDisposition.unavailable ||
          clean == AtlasVaultPairingCleanInstallDisposition.recoveryRequired) {
        return _fixed(
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
          pending: true,
        );
      }
      var transaction = starting;
      final offerArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.offer,
        transaction,
      );
      final acceptanceArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.acceptance,
        transaction,
      );
      final deliveryArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.delivery,
        transaction,
      );
      final signedOffer = _signedOffer(offerArtifact);
      final signedAcceptance = _signedAcceptance(acceptanceArtifact);
      final keyRequest = _keyRequest(acceptanceArtifact);
      final delivery = _delivery(deliveryArtifact);
      final bootstrap = _bootstrap(deliveryArtifact);
      final transcript = await atlasVaultPairingTranscriptSha256(
        signedOffer,
        signedAcceptance,
      );
      if (_hexBytes(transcript) != transaction.transcriptSha256 ||
          delivery.delivery.inviteeDeviceId != identity.deviceId ||
          delivery.delivery.inviterDeviceId != transaction.peerDeviceId) {
        throw const AtlasVaultPairingTransactionException();
      }
      final sessionKey = await _sessionKeyFor(transaction, identity);
      final proofs = await deriveAtlasVaultPairingProofs(
        sessionKey: sessionKey,
        transcriptSha256: transcript,
      );
      sessionKey.fillRange(0, sessionKey.length, 0);
      final suppliedInviter = _proof(deliveryArtifact, 'inviter_proof');
      if (!_constantBytes(proofs.inviter, suppliedInviter)) {
        throw const AtlasVaultPairingException();
      }
      suppliedInviter.fillRange(0, suppliedInviter.length, 0);
      final store = AtlasVaultLocalStore.fromJson(<String, Object?>{
        'format': AtlasVaultLocalStore.format,
        'version': AtlasVaultLocalStore.version,
        'store_id': transaction.transactionId,
        'created_at': transaction.createdAt,
        'updated_at': transaction.createdAt,
        'vault_metadata': bootstrap.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in bootstrap.records) record.toJson(),
        ],
      });
      final localStore = _requireInstallDependency(_localStoreIO);
      final secure = _requireInstallDependency(_secureKeyStore);
      final selected = _requireInstallDependency(_selectedVaultStore);
      final vaultId = delivery.delivery.vaultId;
      if (_stageAtLeast(
        transaction,
        AtlasVaultPairingStage.selectionCommitted,
      )) {
        vaultKey = await secure.loadVaultKey(vaultId);
        if (vaultKey == null ||
            vaultKey.length != 32 ||
            transaction.vaultKeySha256 != await atlasVaultSha256Hex(vaultKey)) {
          throw const AtlasVaultPairingTransactionException();
        }
      } else {
        final ephemeral = transaction.ephemeralPrivateKey;
        if (ephemeral == null) {
          throw const AtlasVaultPairingTransactionException();
        }
        try {
          vaultKey = await openAtlasVaultKeyDelivery(
            delivery,
            keyRequest: keyRequest,
            inviteeEphemeralPrivateKey: ephemeral,
            bootstrap: bootstrap,
            transcriptSha256: transcript,
            // The protected delivery hash records the earlier fresh-expiry gate.
            currentTime: keyRequest.request.issuedAt,
          );
        } finally {
          ephemeral.fillRange(0, ephemeral.length, 0);
        }
      }
      await _runtime.validateImportProjection(
        vaultId: vaultId,
        vaultKey: vaultKey,
        store: store,
      );
      final storeHash = await atlasVaultSha256Hex(store.canonicalBytes());
      final keyHash = await atlasVaultSha256Hex(vaultKey);
      var installedStore = store;

      if (!_stageAtLeast(transaction, AtlasVaultPairingStage.storeCreated)) {
        if (transaction.storeSha256 == null) {
          transaction = await _advance(
            transaction,
            transaction.stage,
            <String, Object?>{'store_sha256': storeHash},
          );
        } else if (transaction.storeSha256 != storeHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        final existing = await localStore.read(vaultId);
        if (existing == null) {
          _authorizeSensitiveMutation();
          await localStore.create(vaultId, store);
        } else if (await atlasVaultSha256Hex(existing.canonicalBytes()) !=
            storeHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        final readBack = await localStore.read(vaultId);
        if (readBack == null ||
            await atlasVaultSha256Hex(readBack.canonicalBytes()) != storeHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        installedStore = readBack;
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.storeCreated,
        );
      } else {
        final readBack = await localStore.read(vaultId);
        if (readBack == null || transaction.storeSha256 != storeHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        final readBackHash = await atlasVaultSha256Hex(
          readBack.canonicalBytes(),
        );
        if (readBackHash != storeHash) {
          if (!_stageAtLeast(
                transaction,
                AtlasVaultPairingStage.selectionCommitted,
              ) ||
              !_sameInstalledStoreIdentity(readBack, store)) {
            throw const AtlasVaultPairingTransactionException();
          }
          await _runtime.validateImportProjection(
            vaultId: vaultId,
            vaultKey: vaultKey,
            store: readBack,
          );
        }
        installedStore = readBack;
      }
      if (!_stageAtLeast(transaction, AtlasVaultPairingStage.keyCreated)) {
        loadedKey = await secure.loadVaultKey(vaultId);
        if (transaction.vaultKeySha256 == null) {
          transaction = await _advance(
            transaction,
            transaction.stage,
            <String, Object?>{'vault_key_sha256': keyHash},
          );
        } else if (transaction.vaultKeySha256 != keyHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        if (loadedKey == null) {
          _authorizeSensitiveMutation();
          await secure.createVaultKey(vaultId, vaultKey);
          loadedKey = await secure.loadVaultKey(vaultId);
        }
        if (loadedKey == null ||
            !_constantBytes(loadedKey, vaultKey) ||
            await atlasVaultSha256Hex(loadedKey) != keyHash) {
          throw const AtlasVaultPairingTransactionException();
        }
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.keyCreated,
        );
        _wipeBytes(loadedKey);
        loadedKey = null;
      } else {
        loadedKey = await secure.loadVaultKey(vaultId);
        if (loadedKey == null ||
            transaction.vaultKeySha256 != keyHash ||
            !_constantBytes(loadedKey, vaultKey)) {
          throw const AtlasVaultPairingTransactionException();
        }
        _wipeBytes(loadedKey);
        loadedKey = null;
      }
      if (!_stageAtLeast(
        transaction,
        AtlasVaultPairingStage.selectionCommitted,
      )) {
        final selectedVaultId = await selected.read();
        if (selectedVaultId == null) {
          _authorizeSensitiveMutation();
          await selected.create(vaultId);
        } else if (selectedVaultId != vaultId) {
          throw const AtlasVaultPairingTransactionException();
        }
        if (await selected.read() != vaultId) {
          throw const AtlasVaultPairingTransactionException();
        }
        final transactionWithEphemeralKey = transaction;
        final selectedTransaction = await _advance(
          transactionWithEphemeralKey,
          AtlasVaultPairingStage.selectionCommitted,
          <String, Object?>{
            'ephemeral_private_key': null,
            'selection_committed': true,
          },
        );
        transactionWithEphemeralKey.destroy();
        transaction = selectedTransaction;
      } else if (await selected.read() != vaultId) {
        throw const AtlasVaultPairingTransactionException();
      }
      if (!_stageAtLeast(
        transaction,
        AtlasVaultPairingStage.runtimeActivated,
      )) {
        if (!_runtime.isActiveVault(vaultId)) {
          if (_runtime.isActive) {
            throw const AtlasVaultPairingTransactionException();
          }
          _authorizeSensitiveMutation();
          if (!await _activateInstalledVault(vaultId)) {
            throw const AtlasVaultPairingTransactionException();
          }
        }
        if (!_runtime.isActiveVault(vaultId)) {
          throw const AtlasVaultPairingTransactionException();
        }
        final snapshot = await _runtime.read();
        await _runtime.validateImportProjection(
          vaultId: vaultId,
          vaultKey: vaultKey,
          store: installedStore,
        );
        if (snapshot.savedSearches.length + snapshot.trackerRecords.length >
            installedStore.records.length) {
          throw const AtlasVaultPairingTransactionException();
        }
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.runtimeActivated,
          <String, Object?>{'installed_at': _utc(_now())},
        );
      }
      final installedAt = transaction.installedAt;
      if (installedAt == null) {
        throw const AtlasVaultPairingTransactionException();
      }
      final acknowledgement = await _deterministicAcknowledgement(
        transaction,
        identity,
        delivery,
      );
      final acknowledgementArtifact = _artifact(
        AtlasVaultPairingArtifactKind.acknowledgement,
        <String, Object?>{'signed_acknowledgement': acknowledgement.toJson()},
      );
      final acknowledgementArtifactHash = await atlasVaultSha256Hex(
        acknowledgementArtifact.canonicalBytes(),
      );
      if (transaction.acknowledgementSha256 == null) {
        transaction =
            await _advance(transaction, transaction.stage, <String, Object?>{
              'acknowledgement_sha256': acknowledgementArtifactHash,
              'staged_artifacts': await _mergedStagedJson(
                transaction,
                acknowledgementArtifact,
              ),
            });
      } else if (transaction.acknowledgementSha256 !=
          acknowledgementArtifactHash) {
        throw const AtlasVaultPairingTransactionException();
      }
      await _createStaged(acknowledgementArtifact);
      final restored = await _requireStaged(
        AtlasVaultPairingArtifactKind.acknowledgement,
        transaction,
      );
      if (!_constantBytes(
        restored.canonicalBytes(),
        acknowledgementArtifact.canonicalBytes(),
      )) {
        throw const AtlasVaultPairingTransactionException();
      }
      if (!_stageAtLeast(transaction, AtlasVaultPairingStage.trustCommitted)) {
        await _commitTrust(
          localDeviceId: identity.deviceId,
          peer: AtlasVaultTrustedDevicePeer.fromJson(<String, Object?>{
            'peer_device_id': delivery.inviter.descriptor.deviceId,
            'peer_descriptor': delivery.inviter.toJson(),
            'pairing_transcript_sha256': delivery.delivery.transcriptSha256,
            'linked_at': installedAt,
            'role': 'invitee',
            'vault_id': vaultId,
            'key_epoch': delivery.delivery.keyEpoch,
            'delivery_id': delivery.delivery.deliveryId,
            'acknowledgement_sha256': await atlasVaultSha256Hex(
              acknowledgement.canonicalBytes(),
            ),
          }),
        );
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.trustCommitted,
        );
      }
      if (transaction.stage == AtlasVaultPairingStage.trustCommitted) {
        transaction = await _advance(
          transaction,
          AtlasVaultPairingStage.acknowledgementCreated,
        );
      }
      return _result(
        AtlasVaultTrustedPairingDisposition.acknowledgementReady,
        transaction,
        local: identity,
        peerDeviceId: delivery.inviter.descriptor.deviceId,
        trusted: true,
      );
    } catch (_) {
      return _fixed(
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
        pending: true,
      );
    } finally {
      _wipeBytes(vaultKey);
      _wipeBytes(loadedKey);
      identity?.destroy();
    }
  }

  Future<void> _preflightInviteeDelivery(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingArtifact deliveryArtifact,
  ) async {
    AtlasVaultDeviceIdentity? identity;
    Uint8List? sessionKey;
    Uint8List? suppliedInviter;
    Uint8List? ephemeral;
    Uint8List? vaultKey;
    try {
      identity = await _requireIdentity();
      final offerArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.offer,
        transaction,
      );
      final acceptanceArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.acceptance,
        transaction,
      );
      final signedOffer = _signedOffer(offerArtifact);
      final signedAcceptance = _signedAcceptance(acceptanceArtifact);
      final keyRequest = _keyRequest(acceptanceArtifact);
      final delivery = _delivery(deliveryArtifact);
      final bootstrap = _bootstrap(deliveryArtifact);
      final transcript = await atlasVaultPairingTranscriptSha256(
        signedOffer,
        signedAcceptance,
      );
      if (_hexBytes(transcript) != transaction.transcriptSha256 ||
          delivery.delivery.inviteeDeviceId != identity.deviceId ||
          delivery.delivery.inviterDeviceId != transaction.peerDeviceId) {
        throw const AtlasVaultPairingTransactionException();
      }
      sessionKey = await _sessionKeyFor(transaction, identity);
      final proofs = await deriveAtlasVaultPairingProofs(
        sessionKey: sessionKey,
        transcriptSha256: transcript,
      );
      suppliedInviter = _proof(deliveryArtifact, 'inviter_proof');
      final expectedInviter = proofs.inviter;
      try {
        if (!_constantBytes(expectedInviter, suppliedInviter)) {
          throw const AtlasVaultPairingException();
        }
      } finally {
        expectedInviter.fillRange(0, expectedInviter.length, 0);
      }
      ephemeral = transaction.ephemeralPrivateKey;
      if (ephemeral == null) {
        throw const AtlasVaultPairingTransactionException();
      }
      vaultKey = await openAtlasVaultKeyDelivery(
        delivery,
        keyRequest: keyRequest,
        inviteeEphemeralPrivateKey: ephemeral,
        bootstrap: bootstrap,
        transcriptSha256: transcript,
        currentTime: transaction.deliverySha256 == null
            ? _utc(_now())
            : keyRequest.request.issuedAt,
      );
      final store = AtlasVaultLocalStore.fromJson(<String, Object?>{
        'format': AtlasVaultLocalStore.format,
        'version': AtlasVaultLocalStore.version,
        'store_id': transaction.transactionId,
        'created_at': transaction.createdAt,
        'updated_at': transaction.createdAt,
        'vault_metadata': bootstrap.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in bootstrap.records) record.toJson(),
        ],
      });
      await _runtime.validateImportProjection(
        vaultId: delivery.delivery.vaultId,
        vaultKey: vaultKey,
        store: store,
      );
    } finally {
      _wipeBytes(sessionKey);
      _wipeBytes(suppliedInviter);
      _wipeBytes(ephemeral);
      _wipeBytes(vaultKey);
      identity?.destroy();
    }
  }

  Future<AtlasVaultSignedPairingAcknowledgement> _deterministicAcknowledgement(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultDeviceIdentity identity,
    AtlasVaultSignedVaultKeyDelivery delivery,
  ) {
    final installedAt = transaction.installedAt;
    if (installedAt == null) {
      throw const AtlasVaultPairingTransactionException();
    }
    return createAtlasVaultPairingAcknowledgement(
      invitee: identity,
      acknowledgementId: transaction.transactionId,
      delivery: delivery,
      installedAt: installedAt,
    );
  }

  Future<AtlasVaultTrustedPairingResult?> _resumeGeneratedDeliveryOrphan(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultDeviceIdentity identity,
  ) async {
    final artifact = await _stageStore.read(
      AtlasVaultPairingArtifactKind.delivery,
    );
    if (artifact == null) return null;
    return _runtime.withInteroperabilitySession((session) async {
      final offerArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.offer,
        transaction,
      );
      final acceptanceArtifact = await _requireStaged(
        AtlasVaultPairingArtifactKind.acceptance,
        transaction,
      );
      final signedOffer = _signedOffer(offerArtifact);
      final signedAcceptance = _signedAcceptance(acceptanceArtifact);
      final keyRequest = _keyRequest(acceptanceArtifact);
      final delivery = _delivery(artifact);
      final bootstrap = _bootstrap(artifact);
      final transcript = await atlasVaultPairingTranscriptSha256(
        signedOffer,
        signedAcceptance,
      );
      final value = await verifyAtlasVaultSignedVaultKeyDelivery(delivery);
      final peerDeviceId = transaction.peerDeviceId;
      if (peerDeviceId == null) {
        throw const AtlasVaultPairingTransactionException();
      }
      await verifyAtlasVaultPairingKeyRequest(
        keyRequest,
        transcriptSha256: transcript,
        inviterDeviceId: identity.deviceId,
        inviteeDeviceId: peerDeviceId,
        currentTime: _utc(_now()),
      );
      final currentStore = await session.readCurrentLocalStore();
      final currentProjection = encodeCanonicalJson(<String, Object?>{
        'vault_metadata': currentStore.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in currentStore.records) record.toJson(),
        ],
      });
      final stagedProjection = encodeCanonicalJson(<String, Object?>{
        'vault_metadata': bootstrap.vaultMetadata.toJson(),
        'records': <Object?>[
          for (final record in bootstrap.records) record.toJson(),
        ],
      });
      final sessionKey = await _sessionKeyFor(transaction, identity);
      final proofs = await deriveAtlasVaultPairingProofs(
        sessionKey: sessionKey,
        transcriptSha256: transcript,
      );
      sessionKey.fillRange(0, sessionKey.length, 0);
      final suppliedProof = _proof(artifact, 'inviter_proof');
      final validProof = _constantBytes(proofs.inviter, suppliedProof);
      suppliedProof.fillRange(0, suppliedProof.length, 0);
      final bootstrapHash = await atlasVaultSha256Hex(
        bootstrap.canonicalBytes(),
      );
      if (value.inviterDeviceId != identity.deviceId ||
          value.inviteeDeviceId != peerDeviceId ||
          value.transcriptSha256 != _hexBytes(transcript) ||
          value.requestSha256 !=
              await atlasVaultSha256Hex(keyRequest.canonicalBytes()) ||
          value.vaultId != session.vaultId ||
          value.keyEpoch != transaction.keyEpoch ||
          value.bootstrapSha256 != bootstrapHash ||
          value.expiresAt != keyRequest.request.expiresAt ||
          !_constantBytes(currentProjection, stagedProjection) ||
          !validProof) {
        throw const AtlasVaultPairingTransactionException();
      }
      final recovered = await _advance(
        transaction,
        AtlasVaultPairingStage.deliveryCreated,
        <String, Object?>{
          'delivery_sha256': await atlasVaultSha256Hex(
            artifact.canonicalBytes(),
          ),
          'bootstrap_sha256': bootstrapHash,
          'vault_id': session.vaultId,
          'staged_artifacts': await _mergedStagedJson(transaction, artifact),
        },
      );
      return _result(
        AtlasVaultTrustedPairingDisposition.deliveryReady,
        recovered,
        local: identity,
        peerDeviceId: recovered.peerDeviceId,
        sas: await _sasFor(recovered, identity),
        expiresAt: keyRequest.request.expiresAt,
      );
    });
  }

  Future<AtlasVaultTrustedPairingResult> _completeInviterAcknowledgement(
    AtlasVaultPairingTransaction starting,
    AtlasVaultDeviceIdentity identity,
  ) async {
    var transaction = starting;
    final verified = await _verifyInviterAcknowledgement(transaction, identity);
    final acknowledgement = verified.acknowledgement;
    final delivery = verified.delivery;
    if (transaction.stage == AtlasVaultPairingStage.acknowledgementImported) {
      await _consumeReplay(
        identity.deviceId,
        AtlasVaultPairingReplayEntry.fromJson(<String, Object?>{
          'kind': 'acknowledgement',
          'object_id': acknowledgement.acknowledgement.acknowledgementId,
          'transcript_sha256': acknowledgement.acknowledgement.transcriptSha256,
          'consumed_at': _utc(_now()),
          'expires_at': delivery.delivery.expiresAt,
        }),
        acceptExactDuplicate: true,
      );
      transaction = await _advance(
        transaction,
        AtlasVaultPairingStage.acknowledgementConsumed,
      );
    }
    if (transaction.stage == AtlasVaultPairingStage.acknowledgementConsumed) {
      await _commitTrust(
        localDeviceId: identity.deviceId,
        peer: AtlasVaultTrustedDevicePeer.fromJson(<String, Object?>{
          'peer_device_id': acknowledgement.invitee.descriptor.deviceId,
          'peer_descriptor': acknowledgement.invitee.toJson(),
          'pairing_transcript_sha256':
              acknowledgement.acknowledgement.transcriptSha256,
          'linked_at': transaction.createdAt,
          'role': 'inviter',
          'vault_id': acknowledgement.acknowledgement.vaultId,
          'key_epoch': acknowledgement.acknowledgement.keyEpoch,
          'delivery_id': acknowledgement.acknowledgement.deliveryId,
          'acknowledgement_sha256': await atlasVaultSha256Hex(
            acknowledgement.canonicalBytes(),
          ),
        }),
      );
      transaction = await _advance(
        transaction,
        AtlasVaultPairingStage.trustCommitted,
      );
    }
    if (transaction.stage != AtlasVaultPairingStage.trustCommitted) {
      throw const AtlasVaultPairingTransactionException();
    }
    await _clearTransaction(transaction);
    return AtlasVaultTrustedPairingResult(
      disposition: AtlasVaultTrustedPairingDisposition.completed,
      role: AtlasVaultPairingRole.inviter,
      localFingerprint: atlasVaultPairingDeviceFingerprint(identity.deviceId),
      peerFingerprint: atlasVaultPairingDeviceFingerprint(
        acknowledgement.invitee.descriptor.deviceId,
      ),
      trusted: true,
    );
  }

  Future<
    ({
      AtlasVaultSignedPairingAcknowledgement acknowledgement,
      AtlasVaultSignedVaultKeyDelivery delivery,
    })
  >
  _verifyInviterAcknowledgement(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultDeviceIdentity identity, {
    AtlasVaultPairingArtifact? acknowledgementArtifact,
  }) async {
    final candidate =
        acknowledgementArtifact ??
        await _requireStaged(
          AtlasVaultPairingArtifactKind.acknowledgement,
          transaction,
        );
    final deliveryArtifact = await _requireStaged(
      AtlasVaultPairingArtifactKind.delivery,
      transaction,
    );
    final acknowledgement = _acknowledgement(candidate);
    final delivery = _delivery(deliveryArtifact);
    final expectedInvitee = delivery.delivery.inviteeDeviceId;
    if (transaction.role != AtlasVaultPairingRole.inviter ||
        transaction.peerDeviceId != expectedInvitee ||
        acknowledgement.invitee.descriptor.deviceId != expectedInvitee ||
        acknowledgement.acknowledgement.inviteeDeviceId != expectedInvitee) {
      throw const AtlasVaultPairingTransactionException();
    }
    await verifyAtlasVaultPairingAcknowledgement(
      acknowledgement,
      delivery: delivery,
      inviterDeviceId: identity.deviceId,
      inviteeDeviceId: expectedInvitee,
    );
    return (acknowledgement: acknowledgement, delivery: delivery);
  }

  Future<T> _run<T>(Future<T> Function() operation) {
    if (_stopped || _operation != null) {
      return Future<T>.error(const AtlasVaultPairingTransactionException());
    }
    final generation = ++_operationGeneration;
    final completer = Completer<T>();
    late final Future<void> retained;
    retained = () async {
      try {
        if (_stopped) {
          throw const AtlasVaultPairingTransactionException();
        }
        completer.complete(
          await runZoned(
            operation,
            zoneValues: <Object, Object>{_operationLeaseKey: generation},
          ),
        );
      } catch (error, stackTrace) {
        completer.completeError(error, stackTrace);
      } finally {
        if (identical(_operation, retained)) {
          _operation = null;
        }
      }
    }();
    _operation = retained;
    return completer.future;
  }

  void _authorizeSensitiveMutation() {
    if (_stopped || Zone.current[_operationLeaseKey] != _operationGeneration) {
      throw const AtlasVaultPairingTransactionException();
    }
  }

  AtlasVaultTrustedPairingResult _fixed(
    AtlasVaultTrustedPairingDisposition disposition, {
    bool pending = false,
  }) => AtlasVaultTrustedPairingResult(
    disposition: disposition,
    pendingTransaction: pending,
  );

  AtlasVaultTrustedPairingResult? _cleanInstallResult(
    AtlasVaultPairingCleanInstallDisposition disposition,
  ) => switch (disposition) {
    AtlasVaultPairingCleanInstallDisposition.clean => null,
    AtlasVaultPairingCleanInstallDisposition.migrationRequired => _fixed(
      AtlasVaultTrustedPairingDisposition.migrationRequired,
    ),
    AtlasVaultPairingCleanInstallDisposition.existingVault => _fixed(
      AtlasVaultTrustedPairingDisposition.existingVault,
    ),
    AtlasVaultPairingCleanInstallDisposition.unavailable => _fixed(
      AtlasVaultTrustedPairingDisposition.unavailable,
    ),
    AtlasVaultPairingCleanInstallDisposition.recoveryRequired => _fixed(
      AtlasVaultTrustedPairingDisposition.recoveryRequired,
    ),
  };

  Future<AtlasVaultDeviceIdentity?> _loadIdentity() async {
    Uint8List? raw;
    Uint8List? canonical;
    AtlasVaultDeviceIdentitySecret? secret;
    try {
      raw = await _identityStore.loadPrimaryIdentity();
      if (raw == null) return null;
      if (raw.isEmpty ||
          raw.length > AtlasDeviceIdentityCustody.maximumSecretByteCount) {
        throw const AtlasVaultPairingStorageException();
      }
      final decoded = jsonDecode(utf8.decode(raw, allowMalformed: false));
      if (decoded is! Map) {
        throw const AtlasVaultPairingStorageException();
      }
      secret = AtlasVaultDeviceIdentitySecret.fromJson(
        decoded.cast<String, Object?>(),
      );
      canonical = secret.canonicalBytes();
      if (!_constantBytes(raw, canonical)) {
        throw const AtlasVaultPairingStorageException();
      }
      return await secret.loadIdentity();
    } catch (_) {
      throw const AtlasVaultPairingStorageException();
    } finally {
      _wipeBytes(raw);
      _wipeBytes(canonical);
      secret?.destroy();
    }
  }

  Future<AtlasVaultDeviceIdentity> _requireIdentity() async {
    final identity = await _loadIdentity();
    if (identity == null) {
      throw const AtlasVaultPairingStorageException();
    }
    return identity;
  }

  Future<AtlasVaultPairingTransaction> _newTransaction({
    required AtlasVaultPairingRole role,
    required AtlasVaultPairingStage stage,
    required String localDeviceId,
    required String createdAt,
    String? peerDeviceId,
    String? transcriptSha256,
    String? offerSha256,
    String? acceptanceSha256,
    String? deliverySha256,
    String? acknowledgementSha256,
    String? bootstrapSha256,
    String? vaultId,
    int? keyEpoch,
    Uint8List? ephemeralPrivateKey,
    String? storeSha256,
    String? vaultKeySha256,
    bool selectionCommitted = false,
    List<AtlasVaultPairingArtifact> stagedArtifacts = const [],
  }) async => AtlasVaultPairingTransaction.fromJson(<String, Object?>{
    'format': _transactionFormat,
    'version': _transactionVersion,
    'transaction_id': _uuidProvider(),
    'revision': _uuidProvider(),
    'parent_revision': null,
    'role': role.encoded,
    'stage': stage.encoded,
    'created_at': createdAt,
    'updated_at': createdAt,
    'installed_at': null,
    'local_device_id': localDeviceId,
    'peer_device_id': peerDeviceId,
    'transcript_sha256': transcriptSha256,
    'offer_sha256': offerSha256,
    'acceptance_sha256': acceptanceSha256,
    'delivery_sha256': deliverySha256,
    'acknowledgement_sha256': acknowledgementSha256,
    'bootstrap_sha256': bootstrapSha256,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'ephemeral_private_key': ephemeralPrivateKey == null
        ? null
        : base64Encode(ephemeralPrivateKey),
    'store_sha256': storeSha256,
    'vault_key_sha256': vaultKeySha256,
    'selection_committed': selectionCommitted,
    'staged_artifacts': await _stagedJson(stagedArtifacts),
  });

  Future<AtlasVaultPairingTransaction> _advance(
    AtlasVaultPairingTransaction current,
    AtlasVaultPairingStage stage, [
    Map<String, Object?> changes = const <String, Object?>{},
  ]) async {
    final replacement = AtlasVaultPairingTransaction.fromJson(<String, Object?>{
      ...current.toJson(),
      ...changes,
      'revision': _uuidProvider(),
      'parent_revision': current.revision,
      'stage': stage.encoded,
      'updated_at': _utc(_now()),
    });
    validateAtlasVaultPairingTransition(current, replacement);
    _authorizeSensitiveMutation();
    await _transactionStore.replace(
      replacement,
      expectedSha256: await atlasVaultSha256Hex(current.canonicalBytes()),
    );
    await _requireTransaction(replacement);
    return replacement;
  }

  Future<AtlasVaultPairingTransaction> _requireStage(
    AtlasVaultPairingRole role,
    AtlasVaultPairingStage stage,
  ) async {
    final transaction = await _transactionStore.read();
    if (transaction == null ||
        transaction.role != role ||
        transaction.stage != stage) {
      transaction?.destroy();
      throw const AtlasVaultPairingTransactionException();
    }
    return transaction;
  }

  Future<void> _requireTransaction(
    AtlasVaultPairingTransaction expected,
  ) async {
    final value = await _transactionStore.read();
    try {
      if (value == null ||
          !_constantBytes(value.canonicalBytes(), expected.canonicalBytes())) {
        throw const AtlasVaultPairingTransactionException();
      }
    } finally {
      value?.destroy();
    }
  }

  Future<void> _createStaged(AtlasVaultPairingArtifact artifact) async {
    final existing = await _stageStore.read(artifact.kind);
    if (existing == null) {
      _authorizeSensitiveMutation();
      await _stageStore.create(artifact);
    } else if (!_constantBytes(
      existing.canonicalBytes(),
      artifact.canonicalBytes(),
    )) {
      throw const AtlasVaultPairingTransactionException();
    }
    final readBack = await _stageStore.read(artifact.kind);
    if (readBack == null ||
        !_constantBytes(readBack.canonicalBytes(), artifact.canonicalBytes())) {
      throw const AtlasVaultPairingTransactionException();
    }
  }

  Future<AtlasVaultPairingArtifact> _requireStaged(
    AtlasVaultPairingArtifactKind kind,
    AtlasVaultPairingTransaction transaction,
  ) async {
    final metadata = transaction.stagedArtifacts
        .where((item) => item.kind == kind)
        .toList();
    if (metadata.length != 1) {
      throw const AtlasVaultPairingTransactionException();
    }
    final artifact = await _stageStore.read(kind);
    if (artifact == null) {
      throw const AtlasVaultPairingTransactionException();
    }
    final bytes = artifact.canonicalBytes();
    if (bytes.length != metadata.single.byteCount ||
        await atlasVaultSha256Hex(bytes) != metadata.single.sha256) {
      throw const AtlasVaultPairingTransactionException();
    }
    return artifact;
  }

  Future<AtlasVaultTrustedPairingResult> _saveArtifact({
    required AtlasVaultPairingRole expectedRole,
    required AtlasVaultPairingStage expectedStage,
    AtlasVaultPairingStage? sideEffectIntentStage,
    required AtlasVaultPairingArtifactKind kind,
    required AtlasVaultPairingStage savedStage,
    required AtlasVaultTrustedPairingDisposition disposition,
  }) async {
    AtlasVaultDeviceIdentity? identity;
    AtlasVaultPairingTransaction? transaction;
    try {
      transaction = await _transactionStore.read();
      if (transaction == null ||
          transaction.role != expectedRole ||
          (transaction.stage != expectedStage &&
              transaction.stage != sideEffectIntentStage)) {
        throw const AtlasVaultPairingTransactionException();
      }
      final artifact = await _requireStaged(kind, transaction);
      _requireCurrentDeliveryForSave(kind, artifact);
      if (sideEffectIntentStage != null && transaction.stage == expectedStage) {
        final prior = transaction;
        transaction = await _advance(prior, sideEffectIntentStage);
        prior.destroy();
      }
      _authorizeSensitiveMutation();
      if (!await _artifactTransport.save(artifact)) {
        return _fixed(
          AtlasVaultTrustedPairingDisposition.cancelled,
          pending: true,
        );
      }
      _requireCurrentDeliveryForSave(kind, artifact);
      final updated = await _advance(transaction, savedStage);
      identity = await _loadIdentity();
      return _result(disposition, updated, local: identity);
    } catch (_) {
      return _fixed(
        AtlasVaultTrustedPairingDisposition.recoveryRequired,
        pending: true,
      );
    } finally {
      identity?.destroy();
      transaction?.destroy();
    }
  }

  void _requireCurrentDeliveryForSave(
    AtlasVaultPairingArtifactKind kind,
    AtlasVaultPairingArtifact artifact,
  ) {
    if (kind != AtlasVaultPairingArtifactKind.delivery) {
      return;
    }
    final expiresAt = _delivery(artifact).delivery.expiresAt;
    if (!_now().toUtc().isBefore(_time(expiresAt))) {
      throw const AtlasVaultPairingTransactionException();
    }
  }

  Future<AtlasVaultTrustedPairingResult> _resultFor(
    AtlasVaultPairingTransaction transaction, {
    AtlasVaultDeviceIdentity? identity,
  }) async {
    final ownedIdentity = identity == null ? await _loadIdentity() : null;
    final local = identity ?? ownedIdentity;
    try {
      final disposition = switch (transaction.stage) {
        AtlasVaultPairingStage.offerCreated =>
          AtlasVaultTrustedPairingDisposition.offerReady,
        AtlasVaultPairingStage.offerSaved =>
          AtlasVaultTrustedPairingDisposition.offerSaved,
        AtlasVaultPairingStage.offerImported ||
        AtlasVaultPairingStage.acceptanceCreated =>
          AtlasVaultTrustedPairingDisposition.acceptanceReady,
        AtlasVaultPairingStage.acceptanceSaved =>
          AtlasVaultTrustedPairingDisposition.codesReady,
        AtlasVaultPairingStage.acceptanceImported =>
          AtlasVaultTrustedPairingDisposition.codesReady,
        AtlasVaultPairingStage.sasConfirmed ||
        AtlasVaultPairingStage.offerConsumed =>
          AtlasVaultTrustedPairingDisposition.codesConfirmed,
        AtlasVaultPairingStage.deliveryCreated =>
          AtlasVaultTrustedPairingDisposition.deliveryReady,
        AtlasVaultPairingStage.deliveryExportStarted =>
          AtlasVaultTrustedPairingDisposition.deliveryReady,
        AtlasVaultPairingStage.deliverySaved =>
          AtlasVaultTrustedPairingDisposition.deliverySaved,
        AtlasVaultPairingStage.deliveryImported ||
        AtlasVaultPairingStage.storeCreated ||
        AtlasVaultPairingStage.keyCreated ||
        AtlasVaultPairingStage.selectionCommitted ||
        AtlasVaultPairingStage.runtimeActivated =>
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
        AtlasVaultPairingStage.trustCommitted ||
        AtlasVaultPairingStage.acknowledgementCreated =>
          AtlasVaultTrustedPairingDisposition.acknowledgementReady,
        AtlasVaultPairingStage.acknowledgementSaved =>
          AtlasVaultTrustedPairingDisposition.acknowledgementSaved,
        AtlasVaultPairingStage.acknowledgementImported ||
        AtlasVaultPairingStage.acknowledgementConsumed =>
          AtlasVaultTrustedPairingDisposition.recoveryRequired,
      };
      String? sas;
      if (local != null &&
          transaction.transcriptSha256 != null &&
          transaction.acceptanceSha256 != null) {
        try {
          sas = await _sasFor(transaction, local);
        } catch (_) {
          sas = null;
        }
      }
      return _result(
        disposition,
        transaction,
        local: local,
        peerDeviceId: transaction.peerDeviceId,
        sas: sas,
        trusted:
            transaction.stage == AtlasVaultPairingStage.trustCommitted ||
            transaction.stage ==
                AtlasVaultPairingStage.acknowledgementCreated ||
            transaction.stage == AtlasVaultPairingStage.acknowledgementSaved,
      );
    } finally {
      ownedIdentity?.destroy();
    }
  }

  AtlasVaultTrustedPairingResult _result(
    AtlasVaultTrustedPairingDisposition disposition,
    AtlasVaultPairingTransaction transaction, {
    AtlasVaultDeviceIdentity? local,
    String? peerDeviceId,
    String? sas,
    String? expiresAt,
    bool trusted = false,
  }) => AtlasVaultTrustedPairingResult(
    disposition: disposition,
    role: transaction.role,
    stage: transaction.stage,
    localFingerprint: local == null
        ? null
        : atlasVaultPairingDeviceFingerprint(local.deviceId),
    peerFingerprint: peerDeviceId == null
        ? null
        : atlasVaultPairingDeviceFingerprint(peerDeviceId),
    sas: sas,
    expiresAt: expiresAt,
    trusted: trusted,
    pendingTransaction: true,
  );

  Future<AtlasVaultTrustedPairingResult> _completeInviteeSasConfirmation(
    AtlasVaultPairingTransaction confirmed,
    AtlasVaultDeviceIdentity identity, {
    required bool acceptExactReplay,
  }) async {
    final offerArtifact = await _requireStaged(
      AtlasVaultPairingArtifactKind.offer,
      confirmed,
    );
    final offer = _signedOffer(offerArtifact).offer;
    await _consumeReplay(
      identity.deviceId,
      AtlasVaultPairingReplayEntry.fromJson(<String, Object?>{
        'kind': 'offer',
        'object_id': offer.offerId,
        'transcript_sha256': confirmed.transcriptSha256,
        'consumed_at': _utc(_now()),
        'expires_at': offer.expiresAt,
      }),
      acceptExactDuplicate: acceptExactReplay,
    );
    final consumed = await _advance(
      confirmed,
      AtlasVaultPairingStage.offerConsumed,
    );
    return _result(
      AtlasVaultTrustedPairingDisposition.codesConfirmed,
      consumed,
      local: identity,
      peerDeviceId: consumed.peerDeviceId,
      sas: await _sasFor(consumed, identity),
      expiresAt: offer.expiresAt,
    );
  }

  Future<Uint8List> _sessionKeyFor(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultDeviceIdentity identity,
  ) async {
    final offer = await _requireStaged(
      AtlasVaultPairingArtifactKind.offer,
      transaction,
    );
    final acceptance = await _requireStaged(
      AtlasVaultPairingArtifactKind.acceptance,
      transaction,
    );
    return deriveAtlasVaultPairingSessionKey(
      localIdentity: identity,
      signedOffer: _signedOffer(offer),
      signedAcceptance: _signedAcceptance(acceptance),
    );
  }

  Future<String> _sasFor(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultDeviceIdentity identity,
  ) async {
    final key = await _sessionKeyFor(transaction, identity);
    try {
      return await deriveAtlasVaultPairingSas(
        key,
        _requiredHex(transaction.transcriptSha256),
      );
    } finally {
      key.fillRange(0, key.length, 0);
    }
  }

  Future<void> _consumeReplay(
    String localDeviceId,
    AtlasVaultPairingReplayEntry entry, {
    bool acceptExactDuplicate = false,
  }) async {
    var replay = await _replayStore.read();
    if (replay == null) {
      replay = AtlasVaultPairingReplayStore.fromJson(<String, Object?>{
        'format': 'atlasvault-pairing-replay',
        'version': 1,
        'local_device_id': localDeviceId,
        'revision': _uuidProvider(),
        'parent_revision': null,
        'created_at': _utc(_now()),
        'updated_at': _utc(_now()),
        'entries': const <Object?>[],
      });
      _authorizeSensitiveMutation();
      await _replayStore.create(replay);
    }
    if (replay.localDeviceId != localDeviceId) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final digest = await atlasVaultSha256Hex(replay.canonicalBytes());
    final consumed = consumeAtlasVaultPairingReplay(
      replay,
      entry,
      revision: _uuidProvider(),
      updatedAt: _utc(_now()),
      currentTime: _utc(_now()),
    );
    if (consumed.outcome == AtlasVaultReplayConsumeOutcome.alreadyConsumed &&
        !acceptExactDuplicate) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    if (consumed.outcome == AtlasVaultReplayConsumeOutcome.consumed) {
      _authorizeSensitiveMutation();
      await _replayStore.replace(consumed.store, expectedSha256: digest);
    }
    final restored = await _replayStore.read();
    if (restored == null ||
        !_constantBytes(
          restored.canonicalBytes(),
          consumed.store.canonicalBytes(),
        )) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Future<void> _commitTrust({
    required String localDeviceId,
    required AtlasVaultTrustedDevicePeer peer,
  }) async {
    var registry = await _registryStore.read();
    if (registry == null) {
      registry = AtlasVaultTrustedDeviceRegistry.fromJson(<String, Object?>{
        'format': 'atlasvault-trusted-device-registry',
        'version': 1,
        'local_device_id': localDeviceId,
        'revision': _uuidProvider(),
        'parent_revision': null,
        'created_at': _utc(_now()),
        'updated_at': _utc(_now()),
        'devices': const <Object?>[],
      });
      _authorizeSensitiveMutation();
      await _registryStore.create(registry);
    }
    if (registry.localDeviceId != localDeviceId) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final digest = await atlasVaultSha256Hex(registry.canonicalBytes());
    final committed = await commitAtlasVaultTrustedDevice(
      registry,
      peer,
      revision: _uuidProvider(),
      updatedAt: _utc(_now()),
    );
    if (committed.outcome == AtlasVaultTrustedDeviceCommitOutcome.committed) {
      _authorizeSensitiveMutation();
      await _registryStore.replace(committed.registry, expectedSha256: digest);
    }
    final restored = await _registryStore.read();
    if (restored == null ||
        !_constantBytes(
          restored.canonicalBytes(),
          committed.registry.canonicalBytes(),
        )) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Future<void> _requireRegistryAdmission({
    required String localDeviceId,
    required String peerDeviceId,
  }) async {
    final registry = await _registryStore.read();
    if (registry == null) return;
    await verifyAtlasVaultTrustedDeviceRegistry(registry);
    if (registry.localDeviceId != localDeviceId ||
        registry.devices.length >= _maximumTrustedPairingPeers ||
        registry.devices.any((device) => device.peerDeviceId == peerDeviceId)) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  bool _sameInstalledStoreIdentity(
    AtlasVaultLocalStore current,
    AtlasVaultLocalStore imported,
  ) {
    return current.storeId == imported.storeId &&
        current.createdAt == imported.createdAt &&
        current.vaultMetadata == imported.vaultMetadata;
  }

  void _presentPairingDeadline({
    required DateTime expiresAt,
    required DateTime currentTime,
  }) {
    final monotonic = _monotonicNow();
    final deadline = _pairingDeadline ??= AtlasVaultPairingMonotonicDeadline(
      wallTime: currentTime,
      monotonicTime: monotonic,
    );
    deadline.present(
      expiresAt: expiresAt,
      currentTime: currentTime,
      monotonicTime: monotonic,
    );
  }

  void _requireLivePairingDeadline({required DateTime expiresAt}) {
    final currentTime = _now();
    final monotonicTime = _monotonicNow();
    final deadline = _pairingDeadline;
    if (deadline == null) {
      final replacement = AtlasVaultPairingMonotonicDeadline(
        wallTime: currentTime,
        monotonicTime: monotonicTime,
      );
      replacement.present(
        expiresAt: expiresAt,
        currentTime: currentTime,
        monotonicTime: monotonicTime,
      );
      _pairingDeadline = replacement;
      return;
    }
    deadline.requireLive(
      currentTime: currentTime,
      monotonicTime: monotonicTime,
    );
  }

  Future<void> _requireLivePairingDeadlineFor(
    AtlasVaultPairingTransaction transaction,
  ) async {
    final offerArtifact = await _requireStaged(
      AtlasVaultPairingArtifactKind.offer,
      transaction,
    );
    _requireLivePairingDeadline(
      expiresAt: _time(_signedOffer(offerArtifact).offer.expiresAt),
    );
  }

  bool _inspectRequiresLivePairingDeadline(AtlasVaultPairingStage stage) =>
      switch (stage) {
        AtlasVaultPairingStage.offerCreated ||
        AtlasVaultPairingStage.offerSaved ||
        AtlasVaultPairingStage.offerImported ||
        AtlasVaultPairingStage.acceptanceCreated ||
        AtlasVaultPairingStage.acceptanceSaved ||
        AtlasVaultPairingStage.acceptanceImported ||
        AtlasVaultPairingStage.sasConfirmed ||
        AtlasVaultPairingStage.offerConsumed => true,
        _ => false,
      };

  Future<void> _clearTransaction(
    AtlasVaultPairingTransaction transaction,
  ) async {
    for (final metadata in transaction.stagedArtifacts.reversed) {
      final artifact = await _stageStore.read(metadata.kind);
      if (artifact == null) {
        continue;
      }
      if (await atlasVaultSha256Hex(artifact.canonicalBytes()) !=
          metadata.sha256) {
        throw const AtlasVaultPairingTransactionException();
      }
      _authorizeSensitiveMutation();
      await _stageStore.delete(metadata.kind, expectedSha256: metadata.sha256);
      if (await _stageStore.read(metadata.kind) != null) {
        throw const AtlasVaultPairingTransactionException();
      }
    }
    _authorizeSensitiveMutation();
    await _transactionStore.delete(
      expectedSha256: await atlasVaultSha256Hex(transaction.canonicalBytes()),
    );
    if (await _transactionStore.read() != null) {
      throw const AtlasVaultPairingTransactionException();
    }
    _pairingDeadline = null;
  }

  Future<List<Object?>> _stagedJson(
    List<AtlasVaultPairingArtifact> artifacts,
  ) async {
    final sorted = List<AtlasVaultPairingArtifact>.from(artifacts)
      ..sort((left, right) => left.kind.index.compareTo(right.kind.index));
    return <Object?>[
      for (final artifact in sorted)
        <String, Object?>{
          'kind': artifact.kind.encoded,
          'sha256': await atlasVaultSha256Hex(artifact.canonicalBytes()),
          'byte_count': artifact.canonicalBytes().length,
        },
    ];
  }

  Future<List<Object?>> _mergedStagedJson(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingArtifact added,
  ) async {
    final values = <AtlasVaultPairingArtifact>[];
    for (final metadata in transaction.stagedArtifacts) {
      if (metadata.kind != added.kind) {
        values.add(await _requireStaged(metadata.kind, transaction));
      }
    }
    values.add(added);
    return _stagedJson(values);
  }

  bool _stageAtLeast(
    AtlasVaultPairingTransaction transaction,
    AtlasVaultPairingStage stage,
  ) {
    final stages = _stagesFor(transaction.role);
    final requiredIndex = stages.indexOf(stage);
    return requiredIndex >= 0 &&
        stages.indexOf(transaction.stage) >= requiredIndex;
  }

  T _requireInstallDependency<T>(T? dependency) {
    if (dependency == null) {
      throw const AtlasVaultPairingTransactionException();
    }
    return dependency;
  }

  AtlasVaultPairingArtifact _artifact(
    AtlasVaultPairingArtifactKind kind,
    Map<String, Object?> payload,
  ) => AtlasVaultPairingArtifact.fromJson(<String, Object?>{
    'format': 'atlasvault-pairing-artifact',
    'version': 1,
    'kind': kind.encoded,
    'payload': payload,
  });

  AtlasVaultSignedPairingOffer _signedOffer(
    AtlasVaultPairingArtifact artifact,
  ) => AtlasVaultSignedPairingOffer.fromJson(
    requireAtlasVaultObject(
      artifact.payload['signed_offer'],
      context: 'signed offer',
    ),
  );

  AtlasVaultSignedPairingAcceptance _signedAcceptance(
    AtlasVaultPairingArtifact artifact,
  ) => AtlasVaultSignedPairingAcceptance.fromJson(
    requireAtlasVaultObject(
      artifact.payload['signed_acceptance'],
      context: 'signed acceptance',
    ),
  );

  AtlasVaultSignedPairingKeyRequest _keyRequest(
    AtlasVaultPairingArtifact artifact,
  ) => AtlasVaultSignedPairingKeyRequest.fromJson(
    requireAtlasVaultObject(
      artifact.payload['signed_key_request'],
      context: 'signed key request',
    ),
  );

  AtlasVaultSignedVaultKeyDelivery _delivery(
    AtlasVaultPairingArtifact artifact,
  ) => AtlasVaultSignedVaultKeyDelivery.fromJson(
    requireAtlasVaultObject(
      artifact.payload['signed_delivery'],
      context: 'signed delivery',
    ),
  );

  AtlasVaultPairingBootstrap _bootstrap(AtlasVaultPairingArtifact artifact) =>
      AtlasVaultPairingBootstrap.fromJson(
        requireAtlasVaultObject(
          artifact.payload['bootstrap'],
          context: 'bootstrap',
        ),
      );

  AtlasVaultSignedPairingAcknowledgement _acknowledgement(
    AtlasVaultPairingArtifact artifact,
  ) => AtlasVaultSignedPairingAcknowledgement.fromJson(
    requireAtlasVaultObject(
      artifact.payload['signed_acknowledgement'],
      context: 'signed acknowledgement',
    ),
  );

  Uint8List _proof(AtlasVaultPairingArtifact artifact, String field) =>
      requireAtlasVaultCanonicalBase64(
        artifact.payload[field],
        field: field,
        exactLength: 32,
      );
}

final class _AcceptingPairingReplayGuard
    implements AtlasVaultPairingReplayGuard {
  const _AcceptingPairingReplayGuard();

  @override
  Future<AtlasVaultPairingReplayOutcome> consume({
    required String offerId,
    required Uint8List transcriptSha256,
    required String expiresAt,
  }) async => AtlasVaultPairingReplayOutcome.accepted;
}

final Stopwatch _pairingMonotonicClock = Stopwatch()..start();

Duration _pairingMonotonicElapsed() => _pairingMonotonicClock.elapsed;

String _securePairingUuid() {
  final bytes = _securePairingBytes(16);
  try {
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final value = _hexBytes(bytes);
    return '${value.substring(0, 8)}-${value.substring(8, 12)}-'
        '${value.substring(12, 16)}-${value.substring(16, 20)}-'
        '${value.substring(20)}';
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}

Uint8List _securePairingBytes(int length) {
  if (length <= 0) {
    throw const AtlasVaultPairingTransactionException();
  }
  final random = Random.secure();
  return Uint8List.fromList(
    List<int>.generate(length, (_) => random.nextInt(256), growable: false),
  );
}

String _utc(DateTime value) {
  final utc = value.toUtc();
  return '${utc.year.toString().padLeft(4, '0')}-'
      '${utc.month.toString().padLeft(2, '0')}-'
      '${utc.day.toString().padLeft(2, '0')}T'
      '${utc.hour.toString().padLeft(2, '0')}:'
      '${utc.minute.toString().padLeft(2, '0')}:'
      '${utc.second.toString().padLeft(2, '0')}Z';
}

String _hexBytes(List<int> value) =>
    value.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();

Uint8List _requiredHex(String? value) {
  if (value == null || !RegExp(r'^[0-9a-f]{64}$').hasMatch(value)) {
    throw const AtlasVaultPairingTransactionException();
  }
  return Uint8List.fromList(<int>[
    for (var offset = 0; offset < value.length; offset += 2)
      int.parse(value.substring(offset, offset + 2), radix: 16),
  ]);
}

bool _constantBytes(List<int> left, List<int> right) {
  var difference = left.length ^ right.length;
  final maximum = left.length > right.length ? left.length : right.length;
  for (var index = 0; index < maximum; index += 1) {
    difference |=
        (index < left.length ? left[index] : 0) ^
        (index < right.length ? right[index] : 0);
  }
  return difference == 0;
}

void _wipeBytes(Uint8List? value) {
  value?.fillRange(0, value.length, 0);
}

List<AtlasVaultPairingStage> _stagesFor(AtlasVaultPairingRole role) =>
    role == AtlasVaultPairingRole.inviter ? _inviterStages : _inviteeStages;

String _requiredDeviceId(Object? value) {
  final text = requireAtlasVaultString(
    value,
    field: 'device_id',
    allowEmpty: false,
  );
  if (!RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(text)) {
    throw const AtlasVaultPairingTransactionException();
  }
  return text;
}

String? _optionalDeviceId(Object? value) =>
    value == null ? null : _requiredDeviceId(value);

String _requiredSha256(Object? value) {
  final text = requireAtlasVaultString(
    value,
    field: 'sha256',
    allowEmpty: false,
  );
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(text)) {
    throw const AtlasVaultPairingTransactionException();
  }
  return text;
}

String? _optionalSha256(Object? value) =>
    value == null ? null : _requiredSha256(value);

String? _optionalUuid(Object? value) => value == null
    ? null
    : requireAtlasVaultCanonicalUuid(value, field: 'parent_revision');

int? _optionalPositiveInt(Object? value) {
  if (value == null) {
    return null;
  }
  final number = requireAtlasVaultInt(value, field: 'key_epoch');
  if (number <= 0 || number > atlasVaultMaximumDeviceKeyEpoch) {
    throw const AtlasVaultPairingTransactionException();
  }
  return number;
}

bool _nullableTextCanAdvance(String? current, String? replacement) =>
    current == null || current == replacement;

bool _nullableIntCanAdvance(int? current, int? replacement) =>
    current == null || current == replacement;

bool _intListsEqual(List<int> left, List<int> right) {
  if (left.length != right.length) {
    return false;
  }
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) {
      return false;
    }
  }
  return true;
}

bool _bytesEqual(Uint8List left, Uint8List right) {
  var difference = left.length ^ right.length;
  final maximum = left.length > right.length ? left.length : right.length;
  for (var index = 0; index < maximum; index += 1) {
    difference |=
        (index < left.length ? left[index] : 0) ^
        (index < right.length ? right[index] : 0);
  }
  return difference == 0;
}

DateTime _time(String value) => DateTime.parse(value);
