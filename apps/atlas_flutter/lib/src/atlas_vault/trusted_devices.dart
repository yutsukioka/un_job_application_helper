import 'dart:convert';
import 'dart:typed_data';

import 'canonical_json.dart';
import 'device_identity.dart';
import 'strict_values.dart';

const _registryFormat = 'atlasvault-trusted-device-registry';
const _replayFormat = 'atlasvault-pairing-replay';
const _stateVersion = 1;
const _maximumPeers = 64;
const _maximumReplayEntries = 2048;

final class AtlasVaultTrustedDeviceStateException implements Exception {
  const AtlasVaultTrustedDeviceStateException();

  @override
  String toString() => 'AtlasVault trusted-device state is invalid.';
}

final class AtlasVaultTrustedDevicePeer {
  AtlasVaultTrustedDevicePeer._({
    required this.peerDeviceId,
    required this.peerDescriptor,
    required this.pairingTranscriptSha256,
    required this.linkedAt,
    required this.role,
    required this.vaultId,
    required this.keyEpoch,
    required this.deliveryId,
    required this.acknowledgementSha256,
  });

  final String peerDeviceId;
  final AtlasVaultSignedDeviceDescriptor peerDescriptor;
  final String pairingTranscriptSha256;
  final String linkedAt;
  final String role;
  final String vaultId;
  final int keyEpoch;
  final String deliveryId;
  final String acknowledgementSha256;

  factory AtlasVaultTrustedDevicePeer.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'peer_device_id',
          'peer_descriptor',
          'pairing_transcript_sha256',
          'linked_at',
          'role',
          'vault_id',
          'key_epoch',
          'delivery_id',
          'acknowledgement_sha256',
        },
        context: 'Trusted-device peer',
      );
      final role = requireAtlasVaultString(
        value['role'],
        field: 'role',
        allowEmpty: false,
      );
      final epoch = requireAtlasVaultInt(
        value['key_epoch'],
        field: 'key_epoch',
      );
      if (!const <String>{'inviter', 'invitee'}.contains(role) ||
          epoch <= 0 ||
          epoch > atlasVaultMaximumDeviceKeyEpoch) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      final peerDeviceId = _deviceId(value['peer_device_id']);
      final descriptor = AtlasVaultSignedDeviceDescriptor.fromJson(
        requireAtlasVaultObject(
          value['peer_descriptor'],
          context: 'Peer descriptor',
        ),
      );
      if (descriptor.descriptor.deviceId != peerDeviceId) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return AtlasVaultTrustedDevicePeer._(
        peerDeviceId: peerDeviceId,
        peerDescriptor: descriptor,
        pairingTranscriptSha256: _sha256(value['pairing_transcript_sha256']),
        linkedAt: requireAtlasVaultUtcSeconds(
          value['linked_at'],
          field: 'linked_at',
        ),
        role: role,
        vaultId: requireAtlasVaultVaultId(value['vault_id']),
        keyEpoch: epoch,
        deliveryId: requireAtlasVaultCanonicalUuid(
          value['delivery_id'],
          field: 'delivery_id',
        ),
        acknowledgementSha256: _sha256(value['acknowledgement_sha256']),
      );
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'peer_device_id': peerDeviceId,
    'peer_descriptor': peerDescriptor.toJson(),
    'pairing_transcript_sha256': pairingTranscriptSha256,
    'linked_at': linkedAt,
    'role': role,
    'vault_id': vaultId,
    'key_epoch': keyEpoch,
    'delivery_id': deliveryId,
    'acknowledgement_sha256': acknowledgementSha256,
  };

  @override
  bool operator ==(Object other) =>
      other is AtlasVaultTrustedDevicePeer &&
      canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultTrustedDevicePeer(<redacted>)';
}

final class AtlasVaultTrustedDeviceRegistry {
  AtlasVaultTrustedDeviceRegistry._({
    required this.localDeviceId,
    required this.revision,
    required this.parentRevision,
    required this.createdAt,
    required this.updatedAt,
    required List<AtlasVaultTrustedDevicePeer> devices,
  }) : devices = List<AtlasVaultTrustedDevicePeer>.unmodifiable(devices);

  final String localDeviceId;
  final String revision;
  final String? parentRevision;
  final String createdAt;
  final String updatedAt;
  final List<AtlasVaultTrustedDevicePeer> devices;

  factory AtlasVaultTrustedDeviceRegistry.fromJson(
    Map<String, Object?> source,
  ) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'local_device_id',
          'revision',
          'parent_revision',
          'created_at',
          'updated_at',
          'devices',
        },
        context: 'Trusted-device registry',
      );
      if (value['format'] != _registryFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _stateVersion) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      final devices = <AtlasVaultTrustedDevicePeer>[
        for (final item in requireAtlasVaultList(
          value['devices'],
          field: 'devices',
        ))
          AtlasVaultTrustedDevicePeer.fromJson(
            requireAtlasVaultObject(item, context: 'Trusted-device peer'),
          ),
      ];
      final local = _deviceId(value['local_device_id']);
      final ids = devices.map((peer) => peer.peerDeviceId).toList();
      final sorted = List<String>.from(ids)..sort();
      if (devices.length > _maximumPeers ||
          ids.toSet().length != ids.length ||
          ids.any((id) => id == local) ||
          !_stringListsEqual(ids, sorted)) {
        throw const AtlasVaultTrustedDeviceStateException();
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
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return AtlasVaultTrustedDeviceRegistry._(
        localDeviceId: local,
        revision: requireAtlasVaultCanonicalUuid(
          value['revision'],
          field: 'revision',
        ),
        parentRevision: _optionalUuid(value['parent_revision']),
        createdAt: createdAt,
        updatedAt: updatedAt,
        devices: devices,
      );
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  factory AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(Uint8List bytes) {
    try {
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultTrustedDeviceRegistry.fromJson(
        _decodeObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _registryFormat,
    'version': _stateVersion,
    'local_device_id': localDeviceId,
    'revision': revision,
    'parent_revision': parentRevision,
    'created_at': createdAt,
    'updated_at': updatedAt,
    'devices': <Object?>[for (final peer in devices) peer.toJson()],
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  bool operator ==(Object other) =>
      other is AtlasVaultTrustedDeviceRegistry &&
      _bytesEqual(canonicalBytes(), other.canonicalBytes());

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;

  @override
  String toString() => 'AtlasVaultTrustedDeviceRegistry(<redacted>)';
}

Future<AtlasVaultTrustedDeviceRegistry> verifyAtlasVaultTrustedDeviceRegistry(
  AtlasVaultTrustedDeviceRegistry registry,
) async {
  try {
    for (final peer in registry.devices) {
      final descriptor = await verifyAtlasVaultSignedDeviceDescriptor(
        peer.peerDescriptor,
      );
      if (descriptor.deviceId != peer.peerDeviceId) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
    }
    return registry;
  } catch (_) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
}

Future<AtlasVaultTrustedDeviceRegistry>
decodeAndVerifyAtlasVaultTrustedDeviceRegistry(Uint8List bytes) async {
  try {
    return verifyAtlasVaultTrustedDeviceRegistry(
      AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(bytes),
    );
  } catch (_) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
}

enum AtlasVaultTrustedDeviceCommitOutcome { committed, alreadyTrusted }

final class AtlasVaultTrustedDeviceCommitResult {
  const AtlasVaultTrustedDeviceCommitResult(this.registry, this.outcome);

  final AtlasVaultTrustedDeviceRegistry registry;
  final AtlasVaultTrustedDeviceCommitOutcome outcome;
}

Future<AtlasVaultTrustedDeviceCommitResult> commitAtlasVaultTrustedDevice(
  AtlasVaultTrustedDeviceRegistry registry,
  AtlasVaultTrustedDevicePeer peer, {
  required String revision,
  required String updatedAt,
}) async {
  try {
    final verified = await verifyAtlasVaultSignedDeviceDescriptor(
      peer.peerDescriptor,
    );
    if (verified.deviceId != peer.peerDeviceId) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final matches = registry.devices
        .where((value) => value.peerDeviceId == peer.peerDeviceId)
        .toList();
    if (matches.isNotEmpty) {
      if (matches.single == peer) {
        return AtlasVaultTrustedDeviceCommitResult(
          registry,
          AtlasVaultTrustedDeviceCommitOutcome.alreadyTrusted,
        );
      }
      throw const AtlasVaultTrustedDeviceStateException();
    }
    if (registry.devices.length >= _maximumPeers) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final nextRevision = requireAtlasVaultCanonicalUuid(
      revision,
      field: 'revision',
    );
    final nextUpdatedAt = requireAtlasVaultUtcSeconds(
      updatedAt,
      field: 'updated_at',
    );
    if (nextRevision == registry.revision ||
        nextRevision == registry.parentRevision ||
        _time(nextUpdatedAt).isBefore(_time(registry.updatedAt))) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final devices = <AtlasVaultTrustedDevicePeer>[...registry.devices, peer]
      ..sort((left, right) => left.peerDeviceId.compareTo(right.peerDeviceId));
    return AtlasVaultTrustedDeviceCommitResult(
      AtlasVaultTrustedDeviceRegistry.fromJson(<String, Object?>{
        ...registry.toJson(),
        'revision': nextRevision,
        'parent_revision': registry.revision,
        'updated_at': nextUpdatedAt,
        'devices': <Object?>[for (final value in devices) value.toJson()],
      }),
      AtlasVaultTrustedDeviceCommitOutcome.committed,
    );
  } catch (_) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
}

final class AtlasVaultPairingReplayEntry {
  AtlasVaultPairingReplayEntry._({
    required this.kind,
    required this.objectId,
    required this.transcriptSha256,
    required this.consumedAt,
    required this.expiresAt,
  });

  final String kind;
  final String objectId;
  final String transcriptSha256;
  final String consumedAt;
  final String expiresAt;

  factory AtlasVaultPairingReplayEntry.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'kind',
          'object_id',
          'transcript_sha256',
          'consumed_at',
          'expires_at',
        },
        context: 'Pairing replay entry',
      );
      final kind = requireAtlasVaultString(
        value['kind'],
        field: 'kind',
        allowEmpty: false,
      );
      final consumedAt = requireAtlasVaultUtcSeconds(
        value['consumed_at'],
        field: 'consumed_at',
      );
      final expiresAt = requireAtlasVaultUtcSeconds(
        value['expires_at'],
        field: 'expires_at',
      );
      if (!const <String>{'offer', 'acknowledgement'}.contains(kind) ||
          !_time(expiresAt).isAfter(_time(consumedAt))) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return AtlasVaultPairingReplayEntry._(
        kind: kind,
        objectId: requireAtlasVaultCanonicalUuid(
          value['object_id'],
          field: 'object_id',
        ),
        transcriptSha256: _sha256(value['transcript_sha256']),
        consumedAt: consumedAt,
        expiresAt: expiresAt,
      );
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'kind': kind,
    'object_id': objectId,
    'transcript_sha256': transcriptSha256,
    'consumed_at': consumedAt,
    'expires_at': expiresAt,
  };

  @override
  bool operator ==(Object other) =>
      other is AtlasVaultPairingReplayEntry &&
      canonicalJsonString(other.toJson()) == canonicalJsonString(toJson());

  @override
  int get hashCode => canonicalJsonString(toJson()).hashCode;
}

final class AtlasVaultPairingReplayStore {
  AtlasVaultPairingReplayStore._({
    required this.localDeviceId,
    required this.revision,
    required this.parentRevision,
    required this.createdAt,
    required this.updatedAt,
    required List<AtlasVaultPairingReplayEntry> entries,
  }) : entries = List<AtlasVaultPairingReplayEntry>.unmodifiable(entries);

  final String localDeviceId;
  final String revision;
  final String? parentRevision;
  final String createdAt;
  final String updatedAt;
  final List<AtlasVaultPairingReplayEntry> entries;

  factory AtlasVaultPairingReplayStore.fromJson(Map<String, Object?> source) {
    try {
      final value = Map<String, Object?>.from(source);
      requireAtlasVaultExactKeys(
        value,
        requiredKeys: const <String>{
          'format',
          'version',
          'local_device_id',
          'revision',
          'parent_revision',
          'created_at',
          'updated_at',
          'entries',
        },
        context: 'Pairing replay store',
      );
      if (value['format'] != _replayFormat ||
          requireAtlasVaultInt(value['version'], field: 'version') !=
              _stateVersion) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      final entries = <AtlasVaultPairingReplayEntry>[
        for (final item in requireAtlasVaultList(
          value['entries'],
          field: 'entries',
        ))
          AtlasVaultPairingReplayEntry.fromJson(
            requireAtlasVaultObject(item, context: 'Replay entry'),
          ),
      ];
      final keys = entries.map((entry) => '${entry.kind}:${entry.objectId}');
      final sorted = List<AtlasVaultPairingReplayEntry>.from(entries)
        ..sort(_compareReplay);
      final createdAt = requireAtlasVaultUtcSeconds(
        value['created_at'],
        field: 'created_at',
      );
      final updatedAt = requireAtlasVaultUtcSeconds(
        value['updated_at'],
        field: 'updated_at',
      );
      if (entries.length > _maximumReplayEntries ||
          keys.toSet().length != entries.length ||
          !_entryListsEqual(entries, sorted) ||
          _time(updatedAt).isBefore(_time(createdAt))) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return AtlasVaultPairingReplayStore._(
        localDeviceId: _deviceId(value['local_device_id']),
        revision: requireAtlasVaultCanonicalUuid(
          value['revision'],
          field: 'revision',
        ),
        parentRevision: _optionalUuid(value['parent_revision']),
        createdAt: createdAt,
        updatedAt: updatedAt,
        entries: entries,
      );
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  factory AtlasVaultPairingReplayStore.fromCanonicalBytes(Uint8List bytes) {
    try {
      final input = Uint8List.fromList(bytes);
      final decoded = AtlasVaultPairingReplayStore.fromJson(
        _decodeObject(input),
      );
      if (!_bytesEqual(input, decoded.canonicalBytes())) {
        throw const AtlasVaultTrustedDeviceStateException();
      }
      return decoded;
    } catch (_) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _replayFormat,
    'version': _stateVersion,
    'local_device_id': localDeviceId,
    'revision': revision,
    'parent_revision': parentRevision,
    'created_at': createdAt,
    'updated_at': updatedAt,
    'entries': <Object?>[for (final entry in entries) entry.toJson()],
  };

  Uint8List canonicalBytes() => encodeCanonicalJson(toJson());

  @override
  String toString() => 'AtlasVaultPairingReplayStore(<redacted>)';
}

enum AtlasVaultReplayConsumeOutcome { consumed, alreadyConsumed }

final class AtlasVaultReplayConsumeResult {
  const AtlasVaultReplayConsumeResult(this.store, this.outcome);

  final AtlasVaultPairingReplayStore store;
  final AtlasVaultReplayConsumeOutcome outcome;
}

AtlasVaultReplayConsumeResult consumeAtlasVaultPairingReplay(
  AtlasVaultPairingReplayStore store,
  AtlasVaultPairingReplayEntry entry, {
  required String revision,
  required String updatedAt,
  required String currentTime,
}) {
  try {
    final existing = store.entries
        .where(
          (value) =>
              value.kind == entry.kind && value.objectId == entry.objectId,
        )
        .toList();
    if (existing.isNotEmpty) {
      if (existing.single.transcriptSha256 == entry.transcriptSha256) {
        return AtlasVaultReplayConsumeResult(
          store,
          AtlasVaultReplayConsumeOutcome.alreadyConsumed,
        );
      }
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final now = _time(
      requireAtlasVaultUtcSeconds(currentTime, field: 'current_time'),
    );
    if (!now.isBefore(_time(entry.expiresAt))) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    final entries = <AtlasVaultPairingReplayEntry>[
      ...store.entries.where((value) => now.isBefore(_time(value.expiresAt))),
    ];
    if (entries.length >= _maximumReplayEntries) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    entries.add(entry);
    entries.sort(_compareReplay);
    final nextRevision = requireAtlasVaultCanonicalUuid(
      revision,
      field: 'revision',
    );
    final nextUpdatedAt = requireAtlasVaultUtcSeconds(
      updatedAt,
      field: 'updated_at',
    );
    if (nextRevision == store.revision ||
        nextRevision == store.parentRevision ||
        _time(nextUpdatedAt).isBefore(_time(store.updatedAt))) {
      throw const AtlasVaultTrustedDeviceStateException();
    }
    return AtlasVaultReplayConsumeResult(
      AtlasVaultPairingReplayStore.fromJson(<String, Object?>{
        ...store.toJson(),
        'revision': nextRevision,
        'parent_revision': store.revision,
        'updated_at': nextUpdatedAt,
        'entries': <Object?>[for (final value in entries) value.toJson()],
      }),
      AtlasVaultReplayConsumeOutcome.consumed,
    );
  } catch (_) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
}

int _compareReplay(
  AtlasVaultPairingReplayEntry left,
  AtlasVaultPairingReplayEntry right,
) {
  final expiry = left.expiresAt.compareTo(right.expiresAt);
  if (expiry != 0) return expiry;
  final kind = left.kind.compareTo(right.kind);
  return kind != 0 ? kind : left.objectId.compareTo(right.objectId);
}

String _deviceId(Object? value) {
  final text = requireAtlasVaultString(
    value,
    field: 'device_id',
    allowEmpty: false,
  );
  if (!RegExp(r'^avd1-[0-9a-f]{64}$').hasMatch(text)) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
  return text;
}

String _sha256(Object? value) {
  final text = requireAtlasVaultString(
    value,
    field: 'sha256',
    allowEmpty: false,
  );
  if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(text)) {
    throw const AtlasVaultTrustedDeviceStateException();
  }
  return text;
}

String? _optionalUuid(Object? value) => value == null
    ? null
    : requireAtlasVaultCanonicalUuid(value, field: 'parent_revision');

DateTime _time(String value) => DateTime.parse(value);

Map<String, Object?> _decodeObject(Uint8List bytes) {
  final value = jsonDecode(utf8.decode(bytes, allowMalformed: false));
  return requireAtlasVaultObject(value, context: 'Trusted-device state');
}

bool _stringListsEqual(List<String> left, List<String> right) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index++) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

bool _entryListsEqual(
  List<AtlasVaultPairingReplayEntry> left,
  List<AtlasVaultPairingReplayEntry> right,
) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index++) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

bool _bytesEqual(List<int> left, List<int> right) {
  if (left.length != right.length) return false;
  var difference = 0;
  for (var index = 0; index < left.length; index++) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}
