import 'dart:collection';
import 'dart:convert';
import 'dart:typed_data';

import 'crypto.dart';
import 'hpke_key_delivery.dart';

const atlasVaultMaximumKeyRingEntries = 32;
const _maximumKeyEpoch = 0x7fffffffffffffff;
const _keyByteCount = 32;
const _maximumIdentifierByteCount = 1024;
const _metadataFormat = 'atlasvault-vault-key-ring';
const _metadataVersion = 1;
const _hpkeEpochContextPrefix = 'atlasvault-key-epoch-hpke-v1:';
const _recordSaltPrefix = 'atlasvault-record-key-epoch-v1:';

final class AtlasVaultKeyEpochException implements Exception {
  const AtlasVaultKeyEpochException();

  @override
  String toString() => 'AtlasVault key epoch operation failed.';
}

final class AtlasVaultKeyRingMetadata {
  AtlasVaultKeyRingMetadata({
    required int currentKeyEpoch,
    Iterable<int> retainedKeyEpochs = const <int>[],
  }) : currentKeyEpoch = _requireEpoch(currentKeyEpoch),
       retainedKeyEpochs = List<int>.unmodifiable(
         retainedKeyEpochs.map(_requireEpoch),
       ) {
    final retained = this.retainedKeyEpochs;
    if (retained.length + 1 > atlasVaultMaximumKeyRingEntries ||
        retained.toSet().length != retained.length ||
        !_isSorted(retained) ||
        retained.any((epoch) => epoch >= this.currentKeyEpoch)) {
      throw const AtlasVaultKeyEpochException();
    }
  }

  factory AtlasVaultKeyRingMetadata.fromJson(Map<String, Object?> value) {
    if (value.keys.toSet().difference(const <String>{
          'format',
          'version',
          'current_key_epoch',
          'retained_key_epochs',
        }).isNotEmpty ||
        value.length != 4 ||
        value['format'] != _metadataFormat ||
        value['version'] is! int ||
        value['version'] != _metadataVersion ||
        value['current_key_epoch'] is! int ||
        value['retained_key_epochs'] is! List<Object?>) {
      throw const AtlasVaultKeyEpochException();
    }
    final retained = value['retained_key_epochs']! as List<Object?>;
    if (retained.any((value) => value is! int)) {
      throw const AtlasVaultKeyEpochException();
    }
    return AtlasVaultKeyRingMetadata(
      currentKeyEpoch: value['current_key_epoch']! as int,
      retainedKeyEpochs: retained.cast<int>(),
    );
  }

  final int currentKeyEpoch;
  final List<int> retainedKeyEpochs;

  Map<String, Object?> toJson() => <String, Object?>{
    'format': _metadataFormat,
    'version': _metadataVersion,
    'current_key_epoch': currentKeyEpoch,
    'retained_key_epochs': List<int>.from(retainedKeyEpochs),
  };
}

final class AtlasVaultEpochVaultKey {
  AtlasVaultEpochVaultKey({required int keyEpoch, required List<int> vaultKey})
    : keyEpoch = _requireEpoch(keyEpoch),
      vaultKey = _copyKey(vaultKey);

  final int keyEpoch;
  final Uint8List vaultKey;
}

final class AtlasVaultEpochHPKESealedVaultKeyV2 {
  AtlasVaultEpochHPKESealedVaultKeyV2({
    required int keyEpoch,
    required List<int> encapsulatedKey,
    required List<int> ciphertext,
  }) : keyEpoch = _requireEpoch(keyEpoch),
       encapsulatedKey = _copyExact(encapsulatedKey, _keyByteCount),
       ciphertext = _copyExact(ciphertext, 48);

  final int keyEpoch;
  final Uint8List encapsulatedKey;
  final Uint8List ciphertext;
}

final class AtlasVaultKeyEpochRing {
  AtlasVaultKeyEpochRing({
    required AtlasVaultKeyRingMetadata metadata,
    required Map<int, Uint8List> keys,
  }) : metadata = metadata,
       _keys = _validatedKeys(metadata, keys);

  factory AtlasVaultKeyEpochRing.fromEntries({
    required int currentKeyEpoch,
    required Map<int, Uint8List> keys,
  }) {
    final current = _requireEpoch(currentKeyEpoch);
    if (!keys.containsKey(current)) {
      throw const AtlasVaultKeyEpochException();
    }
    final epochs = keys.keys.map(_requireEpoch).toList()..sort();
    if (epochs.any((epoch) => epoch != current && epoch >= current)) {
      throw const AtlasVaultKeyEpochException();
    }
    return AtlasVaultKeyEpochRing(
      metadata: AtlasVaultKeyRingMetadata(
        currentKeyEpoch: current,
        retainedKeyEpochs: epochs.where((epoch) => epoch != current),
      ),
      keys: keys,
    );
  }

  factory AtlasVaultKeyEpochRing.fromLegacy(
    Uint8List vaultKey, {
    int keyEpoch = 1,
  }) {
    final epoch = _requireEpoch(keyEpoch);
    return AtlasVaultKeyEpochRing.fromEntries(
      currentKeyEpoch: epoch,
      keys: <int, Uint8List>{epoch: vaultKey},
    );
  }

  final AtlasVaultKeyRingMetadata metadata;
  final Map<int, Uint8List> _keys;

  int get currentKeyEpoch => metadata.currentKeyEpoch;

  Uint8List get currentVaultKey => vaultKeyForEpoch(currentKeyEpoch);

  Uint8List vaultKeyForEpoch(int keyEpoch) {
    final key = _keys[_requireEpoch(keyEpoch)];
    if (key == null) {
      throw const AtlasVaultKeyEpochException();
    }
    return Uint8List.fromList(key);
  }

  Future<Uint8List> deriveRecordKey({
    required int keyEpoch,
    required String vaultId,
    required String recordId,
  }) {
    final epoch = _requireEpoch(keyEpoch);
    if (epoch == 1) {
      return deriveAtlasVaultRecordKey(
        vaultKey: vaultKeyForEpoch(epoch),
        vaultId: _identifier(vaultId),
        recordId: _identifier(recordId),
      );
    }
    return atlasVaultDeriveHkdfSha256Internal(
      inputKeyMaterial: vaultKeyForEpoch(epoch),
      salt: utf8.encode('$_recordSaltPrefix${_identifier(vaultId)}'),
      info: utf8.encode('epoch:$epoch:record:${_identifier(recordId)}'),
    );
  }

  Future<AtlasVaultEpochHPKESealedVaultKeyV2> sealCurrentHPKEV2({
    required Uint8List recipientPublicKey,
    required Uint8List context,
  }) async {
    try {
      final sealed = await sealAtlasVaultHPKEVaultKeyV2(
        recipientPublicKey: recipientPublicKey,
        vaultKey: currentVaultKey,
        context: _epochContext(currentKeyEpoch, context),
      );
      return _epochSealed(currentKeyEpoch, sealed);
    } catch (_) {
      throw const AtlasVaultKeyEpochException();
    }
  }
}

Future<AtlasVaultEpochHPKESealedVaultKeyV2>
sealAtlasVaultCurrentEpochHPKEV2ForTesting({
  required AtlasVaultKeyEpochRing ring,
  required Uint8List recipientPublicKey,
  required Uint8List context,
  required Uint8List ephemeralPrivateKey,
}) async {
  try {
    final sealed = await sealAtlasVaultHPKEVaultKeyV2ForTesting(
      recipientPublicKey: recipientPublicKey,
      vaultKey: ring.currentVaultKey,
      context: _epochContext(ring.currentKeyEpoch, context),
      ephemeralPrivateKey: ephemeralPrivateKey,
    );
    return _epochSealed(ring.currentKeyEpoch, sealed);
  } catch (_) {
    throw const AtlasVaultKeyEpochException();
  }
}

Future<AtlasVaultEpochVaultKey> openAtlasVaultEpochHPKEV2({
  required Uint8List recipientPrivateKey,
  required AtlasVaultEpochHPKESealedVaultKeyV2 sealed,
  required Uint8List context,
  required int minimumKeyEpoch,
}) async {
  try {
    final minimum = _requireEpoch(minimumKeyEpoch);
    if (sealed.keyEpoch < minimum) {
      throw const AtlasVaultKeyEpochException();
    }
    final vaultKey = await openAtlasVaultHPKEVaultKeyV2(
      recipientPrivateKey: recipientPrivateKey,
      sealed: AtlasVaultHPKESealedVaultKeyV2(
        encapsulatedKey: sealed.encapsulatedKey,
        ciphertext: sealed.ciphertext,
      ),
      context: _epochContext(sealed.keyEpoch, context),
    );
    return AtlasVaultEpochVaultKey(
      keyEpoch: sealed.keyEpoch,
      vaultKey: vaultKey,
    );
  } catch (_) {
    throw const AtlasVaultKeyEpochException();
  }
}

Map<int, Uint8List> _validatedKeys(
  AtlasVaultKeyRingMetadata metadata,
  Map<int, Uint8List> keys,
) {
  final copied = <int, Uint8List>{
    for (final entry in keys.entries)
      _requireEpoch(entry.key): _copyKey(entry.value),
  };
  final expected = <int>{
    ...metadata.retainedKeyEpochs,
    metadata.currentKeyEpoch,
  };
  if (copied.keys.toSet().difference(expected).isNotEmpty ||
      expected.difference(copied.keys.toSet()).isNotEmpty ||
      copied.length > atlasVaultMaximumKeyRingEntries ||
      copied.values.map(base64Encode).toSet().length != copied.length) {
    throw const AtlasVaultKeyEpochException();
  }
  return UnmodifiableMapView<int, Uint8List>(copied);
}

AtlasVaultEpochHPKESealedVaultKeyV2 _epochSealed(
  int keyEpoch,
  AtlasVaultHPKESealedVaultKeyV2 sealed,
) => AtlasVaultEpochHPKESealedVaultKeyV2(
  keyEpoch: keyEpoch,
  encapsulatedKey: sealed.encapsulatedKey,
  ciphertext: sealed.ciphertext,
);

Uint8List _epochContext(int keyEpoch, List<int> context) {
  if (context.isEmpty) {
    throw const AtlasVaultKeyEpochException();
  }
  final epochBytes = Uint8List(8);
  ByteData.sublistView(
    epochBytes,
  ).setInt64(0, _requireEpoch(keyEpoch), Endian.big);
  return Uint8List.fromList(<int>[
    ...ascii.encode(_hpkeEpochContextPrefix),
    ...epochBytes,
    0x3a,
    ...context,
  ]);
}

int _requireEpoch(int value) {
  if (value < 1 || value > _maximumKeyEpoch) {
    throw const AtlasVaultKeyEpochException();
  }
  return value;
}

String _identifier(String value) {
  final encoded = utf8.encode(value);
  if (encoded.isEmpty || encoded.length > _maximumIdentifierByteCount) {
    throw const AtlasVaultKeyEpochException();
  }
  return value;
}

Uint8List _copyKey(List<int> value) => _copyExact(value, _keyByteCount);

Uint8List _copyExact(List<int> value, int length) {
  if (value.length != length) {
    throw const AtlasVaultKeyEpochException();
  }
  return Uint8List.fromList(value);
}

bool _isSorted(List<int> value) {
  for (var index = 1; index < value.length; index += 1) {
    if (value[index - 1] >= value[index]) {
      return false;
    }
  }
  return true;
}
