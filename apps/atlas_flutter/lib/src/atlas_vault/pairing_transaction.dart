import 'dart:convert';
import 'dart:typed_data';

import 'canonical_json.dart';
import 'key_delivery.dart';
import 'strict_values.dart';
import 'trusted_devices.dart';

const _transactionFormat = 'atlasvault-pairing-transaction';
const _transactionVersion = 1;
const atlasVaultMaximumPairingStateByteCount = 2 * 1024 * 1024;
const atlasVaultMaximumPairingTransactionByteCount = 64 * 1024;
const atlasVaultMaximumPairingArtifactByteCount = 128 * 1024 * 1024;

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
      if (_time(updatedAt).isBefore(_time(createdAt))) {
        throw const AtlasVaultPairingTransactionException();
      }
      final encodedPrivateKey = value['ephemeral_private_key'];
      if (encodedPrivateKey != null) {
        ephemeralPrivateKey = requireAtlasVaultCanonicalBase64(
          encodedPrivateKey,
          field: 'ephemeral_private_key',
          exactLength: 32,
        );
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
      final keyEpoch = _optionalPositiveInt(value['key_epoch']);
      final selectionCommitted = requireAtlasVaultBool(
        value['selection_committed'],
        field: 'selection_committed',
      );
      if (selectionCommitted && role != AtlasVaultPairingRole.invitee) {
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
      if (bytes.isEmpty ||
          bytes.length > atlasVaultMaximumPairingTransactionByteCount) {
        throw const AtlasVaultPairingTransactionException();
      }
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
  if (number <= 0 || number > 0x7fffffff) {
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
