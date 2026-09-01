import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:cryptography/cryptography.dart';
import 'package:pointycastle/export.dart' show SHA256Digest;

import '../cache_file_replacement.dart';

const _patchFormat = 'atlasvault-encrypted-patch-operation';
const _opaqueEnvelopeFormat = 'atlasvault-opaque-ciphertext-envelope';
const _queueEnvelopeFormat = 'atlasvault-encrypted-transfer-queue';
const _snapshotFormat = 'atlasvault-authenticated-collection-snapshot';
const _snapshotPayloadFormat =
    'atlasvault-authenticated-collection-snapshot-payload';
const _snapshotAuthenticationAlgorithm = 'HMAC-SHA256';
const _collectionStateFormat = 'atlasvault-encrypted-patch-collection-state';
const _convergentReplicaStateFormat =
    'atlasvault-encrypted-convergent-replica-state';
const _maximumQueueBytes = 128 * 1024 * 1024;
const _maximumQueueOperations = 65536;
const _maximumEnvelopeFieldBytes = 96 * 1024 * 1024;
const _maximumInteger = 0x7fffffffffffffff;

final class AtlasVaultEncryptedPatchException implements Exception {
  const AtlasVaultEncryptedPatchException();

  @override
  String toString() => 'AtlasVault encrypted patch queue state is invalid.';
}

Never _invalid() => throw const AtlasVaultEncryptedPatchException();

Map<String, Object?> _object(Object? value) {
  if (value is! Map) _invalid();
  final result = <String, Object?>{};
  for (final entry in value.entries) {
    if (entry.key is! String) _invalid();
    result[entry.key! as String] = entry.value;
  }
  return result;
}

void _exact(Map<String, Object?> value, Set<String> keys) {
  if (value.keys.toSet().length != keys.length ||
      !value.keys.toSet().containsAll(keys)) {
    _invalid();
  }
}

String _text(Object? value, {int maximum = 128}) {
  if (value is! String || value.isEmpty || value.length > maximum) _invalid();
  return value;
}

String _identifier(Object? value) {
  final text = _text(value);
  if (text == '*' || !RegExp(r'^[A-Za-z0-9._~-]{1,128}$').hasMatch(text)) {
    _invalid();
  }
  return text;
}

int _positiveInteger(Object? value) {
  if (value is! int || value < 1 || value > _maximumInteger) _invalid();
  return value;
}

int _nonnegativeInteger(Object? value) {
  if (value is! int || value < 0 || value > _maximumInteger) _invalid();
  return value;
}

String _uuid(Object? value) {
  final text = _text(value, maximum: 36);
  if (!RegExp(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
  ).hasMatch(text)) {
    _invalid();
  }
  return text;
}

Uint8List _base64(Object? value, {int? exactLength, int minimumLength = 1}) {
  final text = _text(value, maximum: _maximumEnvelopeFieldBytes * 2);
  late final Uint8List decoded;
  try {
    decoded = Uint8List.fromList(base64Decode(text));
  } on FormatException {
    _invalid();
  }
  if (base64Encode(decoded) != text ||
      decoded.length < minimumLength ||
      decoded.length > _maximumEnvelopeFieldBytes ||
      (exactLength != null && decoded.length != exactLength)) {
    _invalid();
  }
  return decoded;
}

String _sha256Hex(List<int> value) {
  final digest = SHA256Digest().process(Uint8List.fromList(value));
  return digest.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
}

Object? _canonicalValue(Object? value) {
  if (value is Map) {
    final keys = value.keys.map((key) {
      if (key is! String) _invalid();
      return key;
    }).toList()..sort();
    return <String, Object?>{
      for (final key in keys) key: _canonicalValue(value[key]),
    };
  }
  if (value is List) return value.map(_canonicalValue).toList(growable: false);
  if (value == null || value is String || value is num || value is bool) {
    return value;
  }
  _invalid();
}

Uint8List _canonicalJsonBytes(Object? value) =>
    Uint8List.fromList(utf8.encode(jsonEncode(_canonicalValue(value))));

bool _constantTimeEqual(List<int> left, List<int> right) {
  if (left.length != right.length) return false;
  var difference = 0;
  for (var index = 0; index < left.length; index++) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}

final class AtlasVaultOpaqueCiphertextEnvelope {
  AtlasVaultOpaqueCiphertextEnvelope._({
    required this.version,
    required this.objectId,
    required this.revision,
    required this.parentRevision,
    required this.keyEpoch,
    required this.nonceBase64,
    required this.ciphertextBase64,
    required this.aadBase64,
    required this.signatureBase64,
    required this.tombstone,
    required this.contentSha256,
  });

  factory AtlasVaultOpaqueCiphertextEnvelope.fromJson(
    Map<String, Object?> value,
  ) {
    _exact(value, const <String>{
      'format',
      'version',
      'object_id',
      'revision',
      'parent_revision',
      'key_epoch',
      'nonce_b64',
      'ciphertext_b64',
      'aad_b64',
      'signature_b64',
      'tombstone',
      'content_sha256',
    });
    if (value['format'] != _opaqueEnvelopeFormat ||
        value['tombstone'] is! bool) {
      _invalid();
    }
    final parentValue = value['parent_revision'];
    final parent = parentValue == null ? null : _identifier(parentValue);
    final ciphertext = _base64(value['ciphertext_b64'], minimumLength: 16);
    final digest = _text(value['content_sha256'], maximum: 64);
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest) ||
        digest != _sha256Hex(ciphertext)) {
      _invalid();
    }
    return AtlasVaultOpaqueCiphertextEnvelope._(
      version: _positiveInteger(value['version']),
      objectId: _identifier(value['object_id']),
      revision: _identifier(value['revision']),
      parentRevision: parent,
      keyEpoch: _positiveInteger(value['key_epoch']),
      nonceBase64: base64Encode(_base64(value['nonce_b64'], exactLength: 12)),
      ciphertextBase64: base64Encode(ciphertext),
      aadBase64: base64Encode(_base64(value['aad_b64'])),
      signatureBase64: base64Encode(_base64(value['signature_b64'])),
      tombstone: value['tombstone']! as bool,
      contentSha256: digest,
    );
  }

  final int version;
  final String objectId;
  final String revision;
  final String? parentRevision;
  final int keyEpoch;
  final String nonceBase64;
  final String ciphertextBase64;
  final String aadBase64;
  final String signatureBase64;
  final bool tombstone;
  final String contentSha256;

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _opaqueEnvelopeFormat,
    'version': version,
    'object_id': objectId,
    'revision': revision,
    'parent_revision': parentRevision,
    'key_epoch': keyEpoch,
    'nonce_b64': nonceBase64,
    'ciphertext_b64': ciphertextBase64,
    'aad_b64': aadBase64,
    'signature_b64': signatureBase64,
    'tombstone': tombstone,
    'content_sha256': contentSha256,
  };
}

final class AtlasVaultEncryptedPatchOperation
    implements Comparable<AtlasVaultEncryptedPatchOperation> {
  AtlasVaultEncryptedPatchOperation._({
    required this.operationId,
    required this.operationType,
    required this.authorDeviceId,
    required this.authorSequence,
    required this.lamport,
    required this.envelope,
  });

  factory AtlasVaultEncryptedPatchOperation.fromJson(
    Map<String, Object?> value,
  ) {
    _exact(value, const <String>{
      'format',
      'version',
      'operation_id',
      'operation_type',
      'author_device_id',
      'author_sequence',
      'lamport',
      'envelope',
    });
    if (value['format'] != _patchFormat || value['version'] != 1) _invalid();
    final envelope = AtlasVaultOpaqueCiphertextEnvelope.fromJson(
      _object(value['envelope']),
    );
    final expectedType = envelope.tombstone ? 'delete' : 'upsert';
    if (value['operation_type'] != expectedType) _invalid();
    return AtlasVaultEncryptedPatchOperation._(
      operationId: _uuid(value['operation_id']),
      operationType: expectedType,
      authorDeviceId: _identifier(value['author_device_id']),
      authorSequence: _positiveInteger(value['author_sequence']),
      lamport: _positiveInteger(value['lamport']),
      envelope: envelope,
    );
  }

  final String operationId;
  final String operationType;
  final String authorDeviceId;
  final int authorSequence;
  final int lamport;
  final AtlasVaultOpaqueCiphertextEnvelope envelope;

  String get idempotencyKey => operationId;

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _patchFormat,
    'version': 1,
    'operation_id': operationId,
    'operation_type': operationType,
    'author_device_id': authorDeviceId,
    'author_sequence': authorSequence,
    'lamport': lamport,
    'envelope': envelope.toJson(),
  };

  @override
  int compareTo(AtlasVaultEncryptedPatchOperation other) {
    for (final comparison in <int>[
      lamport.compareTo(other.lamport),
      authorDeviceId.compareTo(other.authorDeviceId),
      authorSequence.compareTo(other.authorSequence),
      operationId.compareTo(other.operationId),
    ]) {
      if (comparison != 0) return comparison;
    }
    return 0;
  }

  @override
  bool operator ==(Object other) =>
      other is AtlasVaultEncryptedPatchOperation &&
      jsonEncode(toJson()) == jsonEncode(other.toJson());

  @override
  int get hashCode => jsonEncode(toJson()).hashCode;
}

String _fingerprint(AtlasVaultEncryptedPatchOperation value) =>
    _sha256Hex(utf8.encode(jsonEncode(value.toJson())));

final class _EncryptedQueueFile {
  _EncryptedQueueFile(this.file, Uint8List key, {required String kind})
    : _key = Uint8List.fromList(key),
      _aad = Uint8List.fromList(utf8.encode('$_queueEnvelopeFormat:v1:$kind')) {
    if (key.length != 32) _invalid();
  }

  final File file;
  final Uint8List _key;
  final Uint8List _aad;

  Future<Map<String, Object?>> read(Map<String, Object?> fallback) async {
    try {
      await file.parent.create(recursive: true);
      await recoverInterruptedCacheReplacement(file);
      if (!await file.exists()) return Map<String, Object?>.from(fallback);
      final length = await file.length();
      if (length < 1 || length > _maximumQueueBytes) _invalid();
      final outer = _object(jsonDecode(await file.readAsString()));
      _exact(outer, const <String>{
        'format',
        'version',
        'nonce_b64',
        'ciphertext_b64',
      });
      if (outer['format'] != _queueEnvelopeFormat || outer['version'] != 1) {
        _invalid();
      }
      final nonce = _base64(outer['nonce_b64'], exactLength: 12);
      final combined = _base64(outer['ciphertext_b64'], minimumLength: 16);
      final stateBytes = await AesGcm.with256bits().decrypt(
        SecretBox(
          combined.sublist(0, combined.length - 16),
          nonce: nonce,
          mac: Mac(combined.sublist(combined.length - 16)),
        ),
        secretKey: SecretKey(_key),
        aad: _aad,
      );
      return _object(jsonDecode(utf8.decode(stateBytes)));
    } on AtlasVaultEncryptedPatchException {
      rethrow;
    } catch (_) {
      _invalid();
    }
  }

  Future<void> write(
    Map<String, Object?> state, {
    FutureOr<void> Function()? beforeReplace,
  }) async {
    try {
      final stateBytes = Uint8List.fromList(utf8.encode(jsonEncode(state)));
      if (stateBytes.isEmpty || stateBytes.length > _maximumQueueBytes) {
        _invalid();
      }
      final algorithm = AesGcm.with256bits();
      final nonce = Uint8List.fromList(algorithm.newNonce());
      final sealed = await algorithm.encrypt(
        stateBytes,
        secretKey: SecretKey(_key),
        nonce: nonce,
        aad: _aad,
      );
      final combined = Uint8List.fromList(<int>[
        ...sealed.cipherText,
        ...sealed.mac.bytes,
      ]);
      final encoded = utf8.encode(
        jsonEncode(<String, Object?>{
          'format': _queueEnvelopeFormat,
          'version': 1,
          'nonce_b64': base64Encode(nonce),
          'ciphertext_b64': base64Encode(combined),
        }),
      );
      if (encoded.length > _maximumQueueBytes) _invalid();
      await file.parent.create(recursive: true);
      await recoverInterruptedCacheReplacement(file);
      final staged = cacheReplacementTemporaryFile(file);
      if (await staged.exists()) _invalid();
      try {
        await staged.writeAsBytes(encoded, flush: true);
        if (beforeReplace != null) await beforeReplace();
        await replaceCacheFile(targetFile: file, stagedFile: staged);
      } finally {
        if (await staged.exists()) await staged.delete();
      }
    } on AtlasVaultEncryptedPatchException {
      rethrow;
    } catch (_) {
      _invalid();
    }
  }
}

final class AtlasVaultAuthenticatedCollectionSnapshot {
  AtlasVaultAuthenticatedCollectionSnapshot._({
    required this.collectionId,
    required this.collectionRevision,
    required this.lastOrder,
    required this.records,
    required this.appliedFingerprints,
    required this.authorSequences,
    required this.authenticationTagBase64,
    required this.canonicalPayloadSha256,
  });

  static Future<AtlasVaultAuthenticatedCollectionSnapshot> decode(
    Map<String, Object?> value, {
    required Uint8List authenticationKey,
  }) async {
    if (authenticationKey.length != 32) _invalid();
    _exact(value, const <String>{
      'format',
      'version',
      'payload',
      'authentication',
    });
    if (value['format'] != _snapshotFormat || value['version'] != 1) {
      _invalid();
    }
    final payload = _object(value['payload']);
    final authentication = _object(value['authentication']);
    _exact(authentication, const <String>{'algorithm', 'tag_b64'});
    if (authentication['algorithm'] != _snapshotAuthenticationAlgorithm) {
      _invalid();
    }
    final tag = _base64(authentication['tag_b64'], exactLength: 32);
    final payloadBytes = _canonicalJsonBytes(payload);
    final expected = await Hmac.sha256().calculateMac(
      payloadBytes,
      secretKey: SecretKey(authenticationKey),
    );
    if (!_constantTimeEqual(tag, expected.bytes)) _invalid();
    _exact(payload, const <String>{
      'format',
      'version',
      'collection_id',
      'collection_revision',
      'last_order',
      'records',
      'applied_fingerprints',
      'author_sequences',
      'record_count',
      'live_record_count',
      'tombstone_count',
    });
    if (payload['format'] != _snapshotPayloadFormat ||
        payload['version'] != 1 ||
        payload['last_order'] is! List ||
        payload['records'] is! List) {
      _invalid();
    }
    final revision = _positiveInteger(payload['collection_revision']);
    final rawOrder = payload['last_order']! as List;
    if (rawOrder.length != 4) _invalid();
    final order = <Object?>[
      _positiveInteger(rawOrder[0]),
      _identifier(rawOrder[1]),
      _positiveInteger(rawOrder[2]),
      _uuid(rawOrder[3]),
    ];
    final rawRecords = payload['records']! as List;
    if (rawRecords.length > _maximumQueueOperations) _invalid();
    final records = rawRecords
        .map(
          (item) => AtlasVaultOpaqueCiphertextEnvelope.fromJson(_object(item)),
        )
        .toList(growable: false);
    final recordIds = records.map((item) => item.objectId).toList();
    if (recordIds.toSet().length != records.length ||
        !_sameStrings(recordIds, [...recordIds]..sort())) {
      _invalid();
    }
    final fingerprints = <String, String>{};
    for (final entry in _object(payload['applied_fingerprints']).entries) {
      final digest = _text(entry.value, maximum: 64);
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) _invalid();
      fingerprints[_uuid(entry.key)] = digest;
    }
    if (fingerprints.length != revision ||
        revision > _maximumQueueOperations ||
        !fingerprints.containsKey(order[3])) {
      _invalid();
    }
    final sequences = <String, int>{};
    for (final entry in _object(payload['author_sequences']).entries) {
      sequences[_identifier(entry.key)] = _positiveInteger(entry.value);
    }
    if (sequences.isEmpty ||
        sequences.length > _maximumQueueOperations ||
        sequences.values.fold<int>(0, (sum, value) => sum + value) !=
            revision) {
      _invalid();
    }
    final recordCount = _nonnegativeInteger(payload['record_count']);
    final liveCount = _nonnegativeInteger(payload['live_record_count']);
    final tombstoneCount = _nonnegativeInteger(payload['tombstone_count']);
    if (recordCount != records.length ||
        liveCount != records.where((item) => !item.tombstone).length ||
        tombstoneCount != records.where((item) => item.tombstone).length ||
        liveCount + tombstoneCount != recordCount) {
      _invalid();
    }
    return AtlasVaultAuthenticatedCollectionSnapshot._(
      collectionId: _identifier(payload['collection_id']),
      collectionRevision: revision,
      lastOrder: List<Object?>.unmodifiable(order),
      records: List<AtlasVaultOpaqueCiphertextEnvelope>.unmodifiable(records),
      appliedFingerprints: Map<String, String>.unmodifiable(fingerprints),
      authorSequences: Map<String, int>.unmodifiable(sequences),
      authenticationTagBase64: base64Encode(tag),
      canonicalPayloadSha256: _sha256Hex(payloadBytes),
    );
  }

  static Future<AtlasVaultAuthenticatedCollectionSnapshot> decodeBytes(
    Uint8List value, {
    required Uint8List authenticationKey,
  }) async {
    try {
      return decode(
        _object(jsonDecode(utf8.decode(value))),
        authenticationKey: authenticationKey,
      );
    } catch (_) {
      _invalid();
    }
  }

  static Future<AtlasVaultAuthenticatedCollectionSnapshot> create({
    required String collectionId,
    required Map<String, AtlasVaultOpaqueCiphertextEnvelope> records,
    required Map<String, String> fingerprints,
    required Map<String, int> authorSequences,
    required List<Object?> lastOrder,
    required Uint8List authenticationKey,
  }) async {
    final recordValues = records.keys.toList()..sort();
    final fingerprintKeys = fingerprints.keys.toList()..sort();
    final sequenceKeys = authorSequences.keys.toList()..sort();
    final payload = <String, Object?>{
      'format': _snapshotPayloadFormat,
      'version': 1,
      'collection_id': collectionId,
      'collection_revision': fingerprints.length,
      'last_order': lastOrder,
      'records': recordValues.map((key) => records[key]!.toJson()).toList(),
      'applied_fingerprints': <String, Object?>{
        for (final key in fingerprintKeys) key: fingerprints[key],
      },
      'author_sequences': <String, Object?>{
        for (final key in sequenceKeys) key: authorSequences[key],
      },
      'record_count': records.length,
      'live_record_count': records.values
          .where((item) => !item.tombstone)
          .length,
      'tombstone_count': records.values.where((item) => item.tombstone).length,
    };
    final tag = await Hmac.sha256().calculateMac(
      _canonicalJsonBytes(payload),
      secretKey: SecretKey(authenticationKey),
    );
    return decode(<String, Object?>{
      'format': _snapshotFormat,
      'version': 1,
      'payload': payload,
      'authentication': <String, Object?>{
        'algorithm': _snapshotAuthenticationAlgorithm,
        'tag_b64': base64Encode(tag.bytes),
      },
    }, authenticationKey: authenticationKey);
  }

  final String collectionId;
  final int collectionRevision;
  final List<Object?> lastOrder;
  final List<AtlasVaultOpaqueCiphertextEnvelope> records;
  final Map<String, String> appliedFingerprints;
  final Map<String, int> authorSequences;
  final String authenticationTagBase64;
  final String canonicalPayloadSha256;

  Map<String, Object?> toJson() {
    final fingerprintKeys = appliedFingerprints.keys.toList()..sort();
    final sequenceKeys = authorSequences.keys.toList()..sort();
    final payload = <String, Object?>{
      'format': _snapshotPayloadFormat,
      'version': 1,
      'collection_id': collectionId,
      'collection_revision': collectionRevision,
      'last_order': lastOrder,
      'records': records.map((item) => item.toJson()).toList(),
      'applied_fingerprints': <String, Object?>{
        for (final key in fingerprintKeys) key: appliedFingerprints[key],
      },
      'author_sequences': <String, Object?>{
        for (final key in sequenceKeys) key: authorSequences[key],
      },
      'record_count': records.length,
      'live_record_count': records.where((item) => !item.tombstone).length,
      'tombstone_count': records.where((item) => item.tombstone).length,
    };
    return <String, Object?>{
      'format': _snapshotFormat,
      'version': 1,
      'payload': payload,
      'authentication': <String, Object?>{
        'algorithm': _snapshotAuthenticationAlgorithm,
        'tag_b64': authenticationTagBase64,
      },
    };
  }
}

bool _sameStrings(List<String> left, List<String> right) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index++) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

final class _CollectionReplay {
  _CollectionReplay({
    required this.records,
    required this.fingerprints,
    required this.authorSequences,
    required this.objectRevisions,
    required this.lastOrder,
  });

  factory _CollectionReplay.fromSnapshot(
    AtlasVaultAuthenticatedCollectionSnapshot? snapshot,
  ) {
    if (snapshot == null) {
      return _CollectionReplay(
        records: <String, AtlasVaultOpaqueCiphertextEnvelope>{},
        fingerprints: <String, String>{},
        authorSequences: <String, int>{},
        objectRevisions: <String, String>{},
        lastOrder: null,
      );
    }
    final records = <String, AtlasVaultOpaqueCiphertextEnvelope>{
      for (final item in snapshot.records) item.objectId: item,
    };
    return _CollectionReplay(
      records: records,
      fingerprints: Map<String, String>.from(snapshot.appliedFingerprints),
      authorSequences: Map<String, int>.from(snapshot.authorSequences),
      objectRevisions: <String, String>{
        for (final entry in records.entries) entry.key: entry.value.revision,
      },
      lastOrder: List<Object?>.from(snapshot.lastOrder),
    );
  }

  final Map<String, AtlasVaultOpaqueCiphertextEnvelope> records;
  final Map<String, String> fingerprints;
  final Map<String, int> authorSequences;
  final Map<String, String> objectRevisions;
  List<Object?>? lastOrder;

  bool apply(AtlasVaultEncryptedPatchOperation operation) {
    final digest = _fingerprint(operation);
    final known = fingerprints[operation.operationId];
    if (known != null) {
      if (known != digest) _invalid();
      return false;
    }
    if (fingerprints.length >= _maximumQueueOperations) _invalid();
    lastOrder = _advance(
      operation,
      authorSequences,
      objectRevisions,
      lastOrder,
    );
    records[operation.envelope.objectId] = operation.envelope;
    fingerprints[operation.operationId] = digest;
    return true;
  }
}

final class _LoadedCollection {
  const _LoadedCollection(this.snapshot, this.tail, this.replay);

  final AtlasVaultAuthenticatedCollectionSnapshot? snapshot;
  final List<AtlasVaultEncryptedPatchOperation> tail;
  final _CollectionReplay replay;
}

Map<String, Object?> _collectionDefault(String collectionId) =>
    <String, Object?>{
      'format': _collectionStateFormat,
      'version': 1,
      'collection_id': collectionId,
      'snapshot': null,
      'tail_operations': <Object?>[],
    };

Future<_LoadedCollection> _loadCollection(
  _EncryptedQueueFile store, {
  required String collectionId,
  required Uint8List authenticationKey,
}) async {
  final state = await store.read(_collectionDefault(collectionId));
  _exact(state, const <String>{
    'format',
    'version',
    'collection_id',
    'snapshot',
    'tail_operations',
  });
  if (state['format'] != _collectionStateFormat ||
      state['version'] != 1 ||
      _identifier(state['collection_id']) != collectionId ||
      state['tail_operations'] is! List) {
    _invalid();
  }
  AtlasVaultAuthenticatedCollectionSnapshot? snapshot;
  if (state['snapshot'] != null) {
    snapshot = await AtlasVaultAuthenticatedCollectionSnapshot.decode(
      _object(state['snapshot']),
      authenticationKey: authenticationKey,
    );
    if (snapshot.collectionId != collectionId) _invalid();
  }
  final rawTail = state['tail_operations']! as List;
  if (rawTail.length > _maximumQueueOperations) _invalid();
  final tail = rawTail
      .map((item) => AtlasVaultEncryptedPatchOperation.fromJson(_object(item)))
      .toList(growable: false);
  if (!_sameOperations(tail, [...tail]..sort()) ||
      tail.map((item) => item.operationId).toSet().length != tail.length) {
    _invalid();
  }
  final replay = _CollectionReplay.fromSnapshot(snapshot);
  for (final operation in tail) {
    if (!replay.apply(operation)) _invalid();
  }
  return _LoadedCollection(snapshot, tail, replay);
}

final class AtlasVaultDurableEncryptedPatchCollection {
  AtlasVaultDurableEncryptedPatchCollection(
    File file, {
    required Uint8List encryptionKey,
    required Uint8List authenticationKey,
    required String collectionId,
  }) : _collectionId = _identifier(collectionId),
       _authenticationKey = Uint8List.fromList(authenticationKey),
       _store = _EncryptedQueueFile(file, encryptionKey, kind: 'collection') {
    if (authenticationKey.length != 32) _invalid();
  }

  final String _collectionId;
  final Uint8List _authenticationKey;
  final _EncryptedQueueFile _store;

  Future<_LoadedCollection> _load() => _loadCollection(
    _store,
    collectionId: _collectionId,
    authenticationKey: _authenticationKey,
  );

  Future<void> append(AtlasVaultEncryptedPatchOperation operation) async {
    final loaded = await _load();
    if (!loaded.replay.apply(operation)) return;
    final tail = <AtlasVaultEncryptedPatchOperation>[...loaded.tail, operation];
    await _store.write(<String, Object?>{
      'format': _collectionStateFormat,
      'version': 1,
      'collection_id': _collectionId,
      'snapshot': loaded.snapshot?.toJson(),
      'tail_operations': tail.map((item) => item.toJson()).toList(),
    });
  }

  Future<List<AtlasVaultOpaqueCiphertextEnvelope>> currentRecords() async {
    final replay = (await _load()).replay;
    final keys = replay.records.keys.toList()..sort();
    return List<AtlasVaultOpaqueCiphertextEnvelope>.unmodifiable(
      keys.map((key) => replay.records[key]!),
    );
  }

  Future<List<AtlasVaultEncryptedPatchOperation>> tailOperations() async =>
      List<AtlasVaultEncryptedPatchOperation>.unmodifiable(
        (await _load()).tail,
      );

  Future<AtlasVaultAuthenticatedCollectionSnapshot?> readSnapshot() async =>
      (await _load()).snapshot;

  Future<int> committedOperationCount() async =>
      (await _load()).replay.fingerprints.length;

  Future<AtlasVaultAuthenticatedCollectionSnapshot> compact({
    FutureOr<void> Function()? beforeReplace,
  }) async {
    final replay = (await _load()).replay;
    if (replay.lastOrder == null || replay.fingerprints.isEmpty) _invalid();
    final snapshot = await AtlasVaultAuthenticatedCollectionSnapshot.create(
      collectionId: _collectionId,
      records: replay.records,
      fingerprints: replay.fingerprints,
      authorSequences: replay.authorSequences,
      lastOrder: replay.lastOrder!,
      authenticationKey: _authenticationKey,
    );
    await _store.write(<String, Object?>{
      'format': _collectionStateFormat,
      'version': 1,
      'collection_id': _collectionId,
      'snapshot': snapshot.toJson(),
      'tail_operations': <Object?>[],
    }, beforeReplace: beforeReplace);
    return snapshot;
  }
}

final class _ConvergentReplicaState {
  const _ConvergentReplicaState({
    required this.operations,
    required this.snapshots,
    required this.pendingOperationIds,
    required this.receipts,
  });

  final List<AtlasVaultEncryptedPatchOperation> operations;
  final List<AtlasVaultAuthenticatedCollectionSnapshot> snapshots;
  final List<String> pendingOperationIds;
  final Map<String, String> receipts;
}

Map<String, Object?> _convergentDefault(String collectionId) =>
    <String, Object?>{
      'format': _convergentReplicaStateFormat,
      'version': 1,
      'collection_id': collectionId,
      'operations': <Object?>[],
      'snapshots': <Object?>[],
      'pending_operation_ids': <Object?>[],
    };

Map<String, String> _validateConvergentHistory(
  List<AtlasVaultEncryptedPatchOperation> operations,
  List<AtlasVaultAuthenticatedCollectionSnapshot> snapshots,
) {
  final receipts = <String, String>{};
  final snapshotSequences = <String, int>{};
  final revisionValues = <String, String>{};
  final revisionParents = <String, String?>{};

  void addReceipt(String operationId, String digest) {
    final known = receipts[operationId];
    if (known != null && known != digest) _invalid();
    receipts[operationId] = digest;
  }

  void addEnvelope(AtlasVaultOpaqueCiphertextEnvelope envelope) {
    if (envelope.parentRevision == envelope.revision) _invalid();
    final key = '${envelope.objectId}\u0000${envelope.revision}';
    final encoded = _sha256Hex(_canonicalJsonBytes(envelope.toJson()));
    final known = revisionValues[key];
    if (known != null && known != encoded) _invalid();
    revisionValues[key] = encoded;
    revisionParents[key] = envelope.parentRevision;
  }

  for (final snapshot in snapshots) {
    for (final entry in snapshot.appliedFingerprints.entries) {
      addReceipt(entry.key, entry.value);
    }
    for (final entry in snapshot.authorSequences.entries) {
      final current = snapshotSequences[entry.key] ?? 0;
      if (entry.value > current) snapshotSequences[entry.key] = entry.value;
    }
    for (final envelope in snapshot.records) {
      addEnvelope(envelope);
    }
  }

  final sequenceOwners = <String, String>{};
  for (final operation in operations) {
    final digest = _fingerprint(operation);
    final knownReceipt = receipts[operation.operationId];
    addReceipt(operation.operationId, digest);
    final sequenceKey =
        '${operation.authorDeviceId}\u0000${operation.authorSequence}';
    final knownOwner = sequenceOwners[sequenceKey];
    if (knownOwner != null && knownOwner != operation.operationId) _invalid();
    sequenceOwners[sequenceKey] = operation.operationId;
    if (knownReceipt == null &&
        operation.authorSequence <=
            (snapshotSequences[operation.authorDeviceId] ?? 0)) {
      _invalid();
    }
    addEnvelope(operation.envelope);
  }
  if (receipts.length > _maximumQueueOperations) _invalid();

  for (final start in revisionParents.keys) {
    final separator = start.indexOf('\u0000');
    final objectId = start.substring(0, separator);
    final seen = <String>{};
    String? current = start;
    while (current != null && revisionParents.containsKey(current)) {
      if (!seen.add(current)) _invalid();
      final parent = revisionParents[current];
      current = parent == null ? null : '$objectId\u0000$parent';
    }
  }
  return receipts;
}

Future<_ConvergentReplicaState> _loadConvergentReplica(
  _EncryptedQueueFile store, {
  required String collectionId,
  required Uint8List authenticationKey,
}) async {
  final state = await store.read(_convergentDefault(collectionId));
  _exact(state, const <String>{
    'format',
    'version',
    'collection_id',
    'operations',
    'snapshots',
    'pending_operation_ids',
  });
  if (state['format'] != _convergentReplicaStateFormat ||
      state['version'] != 1 ||
      _identifier(state['collection_id']) != collectionId ||
      state['operations'] is! List ||
      state['snapshots'] is! List ||
      state['pending_operation_ids'] is! List) {
    _invalid();
  }
  final rawOperations = state['operations']! as List;
  final rawSnapshots = state['snapshots']! as List;
  final rawPending = state['pending_operation_ids']! as List;
  if (rawOperations.length > _maximumQueueOperations ||
      rawSnapshots.length > _maximumQueueOperations ||
      rawPending.length > _maximumQueueOperations) {
    _invalid();
  }
  final operations = rawOperations
      .map((item) => AtlasVaultEncryptedPatchOperation.fromJson(_object(item)))
      .toList(growable: false);
  final orderedOperations = [...operations]
    ..sort((left, right) => left.operationId.compareTo(right.operationId));
  if (!_sameOperations(operations, orderedOperations) ||
      operations.map((item) => item.operationId).toSet().length !=
          operations.length) {
    _invalid();
  }
  final snapshots = <AtlasVaultAuthenticatedCollectionSnapshot>[];
  for (final item in rawSnapshots) {
    final snapshot = await AtlasVaultAuthenticatedCollectionSnapshot.decode(
      _object(item),
      authenticationKey: authenticationKey,
    );
    if (snapshot.collectionId != collectionId) _invalid();
    snapshots.add(snapshot);
  }
  final snapshotDigests = snapshots
      .map((item) => item.canonicalPayloadSha256)
      .toList(growable: false);
  if (!_sameStrings(snapshotDigests, [...snapshotDigests]..sort()) ||
      snapshotDigests.toSet().length != snapshotDigests.length) {
    _invalid();
  }
  final pending = rawPending.map(_uuid).toList(growable: false);
  if (!_sameStrings(pending, [...pending]..sort()) ||
      pending.toSet().length != pending.length ||
      !operations
          .map((item) => item.operationId)
          .toSet()
          .containsAll(pending)) {
    _invalid();
  }
  return _ConvergentReplicaState(
    operations: List<AtlasVaultEncryptedPatchOperation>.unmodifiable(
      operations,
    ),
    snapshots: List<AtlasVaultAuthenticatedCollectionSnapshot>.unmodifiable(
      snapshots,
    ),
    pendingOperationIds: List<String>.unmodifiable(pending),
    receipts: Map<String, String>.unmodifiable(
      _validateConvergentHistory(operations, snapshots),
    ),
  );
}

final class _ConvergentCandidate {
  const _ConvergentCandidate({
    required this.envelope,
    required this.operation,
    required this.snapshotDigest,
  });

  final AtlasVaultOpaqueCiphertextEnvelope envelope;
  final AtlasVaultEncryptedPatchOperation? operation;
  final String snapshotDigest;

  bool winsOver(_ConvergentCandidate other) {
    if (envelope.tombstone != other.envelope.tombstone) {
      return envelope.tombstone;
    }
    if (operation != null && other.operation == null) return true;
    if (operation == null && other.operation != null) return false;
    if (operation != null) return operation!.compareTo(other.operation!) > 0;
    return snapshotDigest.compareTo(other.snapshotDigest) > 0;
  }
}

final class AtlasVaultDurableEncryptedConvergentReplica {
  AtlasVaultDurableEncryptedConvergentReplica(
    File file, {
    required Uint8List encryptionKey,
    required Uint8List authenticationKey,
    required String collectionId,
  }) : _collectionId = _identifier(collectionId),
       _authenticationKey = Uint8List.fromList(authenticationKey),
       _store = _EncryptedQueueFile(
         file,
         encryptionKey,
         kind: 'convergent-replica',
       ) {
    if (authenticationKey.length != 32) _invalid();
  }

  final String _collectionId;
  final Uint8List _authenticationKey;
  final _EncryptedQueueFile _store;

  Future<_ConvergentReplicaState> _load() => _loadConvergentReplica(
    _store,
    collectionId: _collectionId,
    authenticationKey: _authenticationKey,
  );

  Future<void> _write({
    required Iterable<AtlasVaultEncryptedPatchOperation> operations,
    required Iterable<AtlasVaultAuthenticatedCollectionSnapshot> snapshots,
    required Iterable<String> pendingOperationIds,
  }) async {
    final orderedOperations = operations.toList()
      ..sort((left, right) => left.operationId.compareTo(right.operationId));
    final orderedSnapshots = snapshots.toList()
      ..sort(
        (left, right) =>
            left.canonicalPayloadSha256.compareTo(right.canonicalPayloadSha256),
      );
    final orderedPending = pendingOperationIds.toList()..sort();
    _validateConvergentHistory(orderedOperations, orderedSnapshots);
    if (!orderedOperations
        .map((item) => item.operationId)
        .toSet()
        .containsAll(orderedPending)) {
      _invalid();
    }
    await _store.write(<String, Object?>{
      'format': _convergentReplicaStateFormat,
      'version': 1,
      'collection_id': _collectionId,
      'operations': orderedOperations.map((item) => item.toJson()).toList(),
      'snapshots': orderedSnapshots.map((item) => item.toJson()).toList(),
      'pending_operation_ids': orderedPending,
    });
  }

  Future<bool> ingestRemote(AtlasVaultEncryptedPatchOperation operation) async {
    final state = await _load();
    final digest = _fingerprint(operation);
    final known = state.receipts[operation.operationId];
    if (known != null) {
      if (known != digest) _invalid();
      return false;
    }
    await _write(
      operations: <AtlasVaultEncryptedPatchOperation>[
        ...state.operations,
        operation,
      ],
      snapshots: state.snapshots,
      pendingOperationIds: state.pendingOperationIds,
    );
    return true;
  }

  Future<bool> queueLocal(AtlasVaultEncryptedPatchOperation operation) async {
    final state = await _load();
    final digest = _fingerprint(operation);
    final known = state.receipts[operation.operationId];
    if (known != null) {
      if (known != digest) _invalid();
      return false;
    }
    await _write(
      operations: <AtlasVaultEncryptedPatchOperation>[
        ...state.operations,
        operation,
      ],
      snapshots: state.snapshots,
      pendingOperationIds: <String>[
        ...state.pendingOperationIds,
        operation.operationId,
      ],
    );
    return true;
  }

  Future<bool> mergeSnapshot(
    AtlasVaultAuthenticatedCollectionSnapshot snapshot,
  ) async {
    final verified = await AtlasVaultAuthenticatedCollectionSnapshot.decode(
      snapshot.toJson(),
      authenticationKey: _authenticationKey,
    );
    if (verified.collectionId != _collectionId) _invalid();
    final state = await _load();
    if (state.snapshots.any(
      (item) => item.canonicalPayloadSha256 == verified.canonicalPayloadSha256,
    )) {
      return false;
    }
    await _write(
      operations: state.operations,
      snapshots: <AtlasVaultAuthenticatedCollectionSnapshot>[
        ...state.snapshots,
        verified,
      ],
      pendingOperationIds: state.pendingOperationIds,
    );
    return true;
  }

  Future<List<AtlasVaultOpaqueCiphertextEnvelope>> currentRecords() async {
    final state = await _load();
    final winners = <String, _ConvergentCandidate>{};
    void consider(_ConvergentCandidate candidate) {
      final objectId = candidate.envelope.objectId;
      final current = winners[objectId];
      if (current == null || candidate.winsOver(current)) {
        winners[objectId] = candidate;
      }
    }

    for (final snapshot in state.snapshots) {
      for (final envelope in snapshot.records) {
        consider(
          _ConvergentCandidate(
            envelope: envelope,
            operation: null,
            snapshotDigest: snapshot.canonicalPayloadSha256,
          ),
        );
      }
    }
    for (final operation in state.operations) {
      consider(
        _ConvergentCandidate(
          envelope: operation.envelope,
          operation: operation,
          snapshotDigest: '',
        ),
      );
    }
    final objectIds = winners.keys.toList()..sort();
    return List<AtlasVaultOpaqueCiphertextEnvelope>.unmodifiable(
      objectIds.map((item) => winners[item]!.envelope),
    );
  }

  Future<int> acceptedOperationCount() async => (await _load()).receipts.length;

  Future<List<AtlasVaultEncryptedPatchOperation>> pendingOperations() async {
    final state = await _load();
    final pending = state.pendingOperationIds.toSet();
    final result =
        state.operations
            .where((item) => pending.contains(item.operationId))
            .toList()
          ..sort();
    return List<AtlasVaultEncryptedPatchOperation>.unmodifiable(result);
  }

  Future<void> confirmRemoteAcceptance(String operationId) async {
    operationId = _uuid(operationId);
    final state = await _load();
    if (!state.pendingOperationIds.contains(operationId)) _invalid();
    await _write(
      operations: state.operations,
      snapshots: state.snapshots,
      pendingOperationIds: state.pendingOperationIds.where(
        (item) => item != operationId,
      ),
    );
  }

  Future<int> synchronizeTo(
    AtlasVaultDurableEncryptedConvergentReplica remote,
  ) async {
    if (remote._collectionId != _collectionId) _invalid();
    var accepted = 0;
    for (final operation in await pendingOperations()) {
      await remote.ingestRemote(operation);
      await confirmRemoteAcceptance(operation.operationId);
      accepted++;
    }
    return accepted;
  }
}

Map<String, Object?> _outboxDefault() => <String, Object?>{
  'format': 'atlasvault-encrypted-outbox-state',
  'version': 1,
  'operations': <Object?>[],
};

Future<List<AtlasVaultEncryptedPatchOperation>> _loadOutbox(
  _EncryptedQueueFile store,
) async {
  final state = await store.read(_outboxDefault());
  _exact(state, const <String>{'format', 'version', 'operations'});
  if (state['format'] != 'atlasvault-encrypted-outbox-state' ||
      state['version'] != 1 ||
      state['operations'] is! List) {
    _invalid();
  }
  final raw = state['operations']! as List;
  if (raw.length > _maximumQueueOperations) _invalid();
  final operations = raw
      .map(
        (value) => AtlasVaultEncryptedPatchOperation.fromJson(_object(value)),
      )
      .toList(growable: true);
  final ordered = [...operations]..sort();
  if (!_sameOperations(operations, ordered) ||
      operations.map((value) => value.operationId).toSet().length !=
          operations.length) {
    _invalid();
  }
  return operations;
}

bool _sameOperations(
  List<AtlasVaultEncryptedPatchOperation> left,
  List<AtlasVaultEncryptedPatchOperation> right,
) {
  if (left.length != right.length) return false;
  for (var index = 0; index < left.length; index++) {
    if (left[index] != right[index]) return false;
  }
  return true;
}

final class AtlasVaultDurableEncryptedOutbox {
  AtlasVaultDurableEncryptedOutbox(
    File file, {
    required Uint8List encryptionKey,
  }) : _store = _EncryptedQueueFile(file, encryptionKey, kind: 'outbox');

  final _EncryptedQueueFile _store;

  Future<List<AtlasVaultEncryptedPatchOperation>> pendingOperations() async =>
      List<AtlasVaultEncryptedPatchOperation>.unmodifiable(
        await _loadOutbox(_store),
      );

  Future<AtlasVaultEncryptedPatchOperation?> nextPending() async {
    final pending = await _loadOutbox(_store);
    return pending.isEmpty ? null : pending.first;
  }

  Future<void> enqueue(AtlasVaultEncryptedPatchOperation operation) async {
    final operations = await _loadOutbox(_store);
    for (final current in operations) {
      if (current.operationId == operation.operationId) {
        if (_fingerprint(current) != _fingerprint(operation)) _invalid();
        return;
      }
    }
    if (operations.length >= _maximumQueueOperations) _invalid();
    operations.add(operation);
    operations.sort();
    await _store.write(<String, Object?>{
      'format': 'atlasvault-encrypted-outbox-state',
      'version': 1,
      'operations': operations.map((value) => value.toJson()).toList(),
    });
  }

  Future<void> confirmRemoteAcceptance(String operationId) async {
    operationId = _uuid(operationId);
    final operations = await _loadOutbox(_store);
    final retained = operations
        .where((value) => value.operationId != operationId)
        .toList();
    if (retained.length == operations.length) _invalid();
    await _store.write(<String, Object?>{
      'format': 'atlasvault-encrypted-outbox-state',
      'version': 1,
      'operations': retained.map((value) => value.toJson()).toList(),
    });
  }
}

final class _InboxState {
  const _InboxState({
    required this.cursor,
    required this.pendingPage,
    required this.pendingNextCursor,
    required this.pending,
    required this.appliedFingerprints,
    required this.authorSequences,
    required this.objectRevisions,
    required this.lastOrder,
  });

  final String? cursor;
  final bool pendingPage;
  final String? pendingNextCursor;
  final List<AtlasVaultEncryptedPatchOperation> pending;
  final Map<String, String> appliedFingerprints;
  final Map<String, int> authorSequences;
  final Map<String, String> objectRevisions;
  final List<Object?>? lastOrder;

  _InboxState copyWith({
    String? cursor,
    bool keepCursor = true,
    bool? pendingPage,
    String? pendingNextCursor,
    bool keepPendingNextCursor = true,
    List<AtlasVaultEncryptedPatchOperation>? pending,
    Map<String, String>? appliedFingerprints,
    Map<String, int>? authorSequences,
    Map<String, String>? objectRevisions,
    List<Object?>? lastOrder,
  }) => _InboxState(
    cursor: keepCursor ? (cursor ?? this.cursor) : cursor,
    pendingPage: pendingPage ?? this.pendingPage,
    pendingNextCursor: keepPendingNextCursor
        ? (pendingNextCursor ?? this.pendingNextCursor)
        : pendingNextCursor,
    pending: pending ?? this.pending,
    appliedFingerprints: appliedFingerprints ?? this.appliedFingerprints,
    authorSequences: authorSequences ?? this.authorSequences,
    objectRevisions: objectRevisions ?? this.objectRevisions,
    lastOrder: lastOrder ?? this.lastOrder,
  );
}

Map<String, Object?> _inboxDefault() => <String, Object?>{
  'format': 'atlasvault-encrypted-inbox-state',
  'version': 1,
  'cursor': null,
  'pending_page': false,
  'pending_next_cursor': null,
  'pending_operations': <Object?>[],
  'applied_fingerprints': <String, Object?>{},
  'author_sequences': <String, Object?>{},
  'object_revisions': <String, Object?>{},
  'last_order': null,
};

String? _cursor(Object? value) {
  if (value == null) return null;
  return _text(value, maximum: 2048);
}

Future<_InboxState> _loadInbox(_EncryptedQueueFile store) async {
  final state = await store.read(_inboxDefault());
  _exact(state, const <String>{
    'format',
    'version',
    'cursor',
    'pending_page',
    'pending_next_cursor',
    'pending_operations',
    'applied_fingerprints',
    'author_sequences',
    'object_revisions',
    'last_order',
  });
  if (state['format'] != 'atlasvault-encrypted-inbox-state' ||
      state['version'] != 1 ||
      state['pending_operations'] is! List) {
    _invalid();
  }
  final pending = (state['pending_operations']! as List)
      .map(
        (value) => AtlasVaultEncryptedPatchOperation.fromJson(_object(value)),
      )
      .toList();
  if (pending.length > _maximumQueueOperations ||
      !_sameOperations(pending, [...pending]..sort()) ||
      pending.map((value) => value.operationId).toSet().length !=
          pending.length) {
    _invalid();
  }
  final fingerprints = <String, String>{};
  for (final entry in _object(state['applied_fingerprints']).entries) {
    final digest = _text(entry.value, maximum: 64);
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) _invalid();
    fingerprints[_uuid(entry.key)] = digest;
  }
  final sequences = <String, int>{};
  for (final entry in _object(state['author_sequences']).entries) {
    sequences[_identifier(entry.key)] = _positiveInteger(entry.value);
  }
  final revisions = <String, String>{};
  for (final entry in _object(state['object_revisions']).entries) {
    revisions[_identifier(entry.key)] = _identifier(entry.value);
  }
  if (fingerprints.length > _maximumQueueOperations ||
      sequences.length > _maximumQueueOperations ||
      revisions.length > _maximumQueueOperations) {
    _invalid();
  }
  List<Object?>? lastOrder;
  if (state['last_order'] != null) {
    if (state['last_order'] is! List ||
        (state['last_order']! as List).length != 4) {
      _invalid();
    }
    final raw = state['last_order']! as List;
    lastOrder = <Object?>[
      _positiveInteger(raw[0]),
      _identifier(raw[1]),
      _positiveInteger(raw[2]),
      _uuid(raw[3]),
    ];
  }
  if (state['pending_page'] is! bool) _invalid();
  final pendingPage = state['pending_page']! as bool;
  final pendingCursor = _cursor(state['pending_next_cursor']);
  if (pending.isNotEmpty != pendingPage) _invalid();
  return _InboxState(
    cursor: _cursor(state['cursor']),
    pendingPage: pendingPage,
    pendingNextCursor: pendingCursor,
    pending: pending,
    appliedFingerprints: fingerprints,
    authorSequences: sequences,
    objectRevisions: revisions,
    lastOrder: lastOrder,
  );
}

Map<String, Object?> _inboxJson(_InboxState state) => <String, Object?>{
  'format': 'atlasvault-encrypted-inbox-state',
  'version': 1,
  'cursor': state.cursor,
  'pending_page': state.pendingPage,
  'pending_next_cursor': state.pendingNextCursor,
  'pending_operations': state.pending.map((value) => value.toJson()).toList(),
  'applied_fingerprints': state.appliedFingerprints,
  'author_sequences': state.authorSequences,
  'object_revisions': state.objectRevisions,
  'last_order': state.lastOrder,
};

int _compareOrder(
  AtlasVaultEncryptedPatchOperation operation,
  List<Object?> order,
) {
  for (final comparison in <int>[
    operation.lamport.compareTo(order[0]! as int),
    operation.authorDeviceId.compareTo(order[1]! as String),
    operation.authorSequence.compareTo(order[2]! as int),
    operation.operationId.compareTo(order[3]! as String),
  ]) {
    if (comparison != 0) return comparison;
  }
  return 0;
}

List<Object?> _advance(
  AtlasVaultEncryptedPatchOperation operation,
  Map<String, int> sequences,
  Map<String, String> revisions,
  List<Object?>? lastOrder,
) {
  if (lastOrder != null && _compareOrder(operation, lastOrder) <= 0) _invalid();
  if (operation.authorSequence !=
      (sequences[operation.authorDeviceId] ?? 0) + 1) {
    _invalid();
  }
  if (operation.envelope.parentRevision !=
      revisions[operation.envelope.objectId]) {
    _invalid();
  }
  sequences[operation.authorDeviceId] = operation.authorSequence;
  revisions[operation.envelope.objectId] = operation.envelope.revision;
  return <Object?>[
    operation.lamport,
    operation.authorDeviceId,
    operation.authorSequence,
    operation.operationId,
  ];
}

final class AtlasVaultDurableEncryptedInbox {
  AtlasVaultDurableEncryptedInbox(File file, {required Uint8List encryptionKey})
    : _store = _EncryptedQueueFile(file, encryptionKey, kind: 'inbox');

  final _EncryptedQueueFile _store;

  Future<String?> readCursor() async => (await _loadInbox(_store)).cursor;

  Future<List<AtlasVaultEncryptedPatchOperation>> pendingOperations() async =>
      List<AtlasVaultEncryptedPatchOperation>.unmodifiable(
        (await _loadInbox(_store)).pending,
      );

  Future<void> stagePage({
    required String? expectedCursor,
    required String? nextCursor,
    required Iterable<AtlasVaultEncryptedPatchOperation> operations,
  }) async {
    expectedCursor = _cursor(expectedCursor);
    nextCursor = _cursor(nextCursor);
    final state = await _loadInbox(_store);
    if (state.pending.isNotEmpty || state.cursor != expectedCursor) _invalid();
    final incoming = operations.toList(growable: false);
    if (incoming.length > _maximumQueueOperations ||
        !_sameOperations(incoming, [...incoming]..sort()) ||
        incoming.map((value) => value.operationId).toSet().length !=
            incoming.length) {
      _invalid();
    }
    final sequences = Map<String, int>.from(state.authorSequences);
    final revisions = Map<String, String>.from(state.objectRevisions);
    var lastOrder = state.lastOrder;
    final fresh = <AtlasVaultEncryptedPatchOperation>[];
    for (final operation in incoming) {
      final digest = _fingerprint(operation);
      final known = state.appliedFingerprints[operation.operationId];
      if (known != null) {
        if (known != digest) _invalid();
        continue;
      }
      lastOrder = _advance(operation, sequences, revisions, lastOrder);
      fresh.add(operation);
    }
    if (state.appliedFingerprints.length + fresh.length >
        _maximumQueueOperations) {
      _invalid();
    }
    await _store.write(
      _inboxJson(
        state.copyWith(
          cursor: fresh.isEmpty ? nextCursor : state.cursor,
          keepCursor: false,
          pendingPage: fresh.isNotEmpty,
          pendingNextCursor: nextCursor,
          keepPendingNextCursor: false,
          pending: fresh,
        ),
      ),
    );
  }

  Future<AtlasVaultEncryptedPatchOperation?> applyNext(
    FutureOr<void> Function(AtlasVaultEncryptedPatchOperation operation) apply,
  ) async {
    final state = await _loadInbox(_store);
    if (state.pending.isEmpty) return null;
    final operation = state.pending.first;
    await apply(operation);
    final sequences = Map<String, int>.from(state.authorSequences);
    final revisions = Map<String, String>.from(state.objectRevisions);
    final lastOrder = _advance(
      operation,
      sequences,
      revisions,
      state.lastOrder,
    );
    final fingerprints = Map<String, String>.from(state.appliedFingerprints)
      ..[operation.operationId] = _fingerprint(operation);
    final remaining = state.pending.sublist(1);
    await _store.write(
      _inboxJson(
        state.copyWith(
          cursor: remaining.isEmpty ? state.pendingNextCursor : state.cursor,
          keepCursor: false,
          pendingPage: remaining.isNotEmpty,
          pendingNextCursor: remaining.isEmpty ? null : state.pendingNextCursor,
          keepPendingNextCursor: false,
          pending: remaining,
          appliedFingerprints: fingerprints,
          authorSequences: sequences,
          objectRevisions: revisions,
          lastOrder: lastOrder,
        ),
      ),
    );
    return operation;
  }
}
