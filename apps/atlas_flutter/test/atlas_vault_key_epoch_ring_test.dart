import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/key_epochs.dart'
    show sealAtlasVaultCurrentEpochHPKEV2ForTesting;
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_vector_loader.dart';

void main() {
  late Map<String, Object?> root;

  setUpAll(() {
    root = loadAtlasVaultVector('atlasvault_key_epoch_vectors_v1.json');
    expect(root['format'], 'atlasvault-key-epoch-vectors');
    expect(root['version'], 1);
  });

  test('epoch ring matches shared metadata and record derivation', () async {
    final ring = await _ring(root);
    final derivation = atlasVaultObject(root['record_derivation']);

    expect(atlasVaultMaximumKeyRingEntries, root['maximum_ring_entries']);
    expect(
      ring.metadata.toJson(),
      atlasVaultObject(atlasVaultObject(root['ring'])['metadata']),
    );
    expect(ring.currentKeyEpoch, 3);
    expect(
      await _sha256Hex(
        await ring.deriveRecordKey(
          keyEpoch: 1,
          vaultId: derivation['vault_id']! as String,
          recordId: derivation['record_id']! as String,
        ),
      ),
      derivation['expected_epoch_1_key_sha256'],
    );
    expect(
      await _sha256Hex(
        await ring.deriveRecordKey(
          keyEpoch: 3,
          vaultId: derivation['vault_id']! as String,
          recordId: derivation['record_id']! as String,
        ),
      ),
      derivation['expected_epoch_3_key_sha256'],
    );
  });

  test('epoch ring migrates legacy and recovers a retained key', () async {
    final legacy = atlasVaultObject(root['legacy_migration']);
    final legacyKey = await _seed(legacy['vault_key_label']! as String);
    final migrated = AtlasVaultKeyEpochRing.fromLegacy(
      legacyKey,
      keyEpoch: legacy['key_epoch']! as int,
    );

    expect(
      migrated.metadata.toJson(),
      atlasVaultObject(legacy['expected_metadata']),
    );
    expect(migrated.currentVaultKey, legacyKey);
    final ring = await _ring(root);
    expect(ring.vaultKeyForEpoch(1), legacyKey);
    expect(
      () => ring.vaultKeyForEpoch(4),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
  });

  test('migrated epoch one preserves legacy record-key derivation', () async {
    final vaultKey = await _seed('legacy-record-key');
    final migrated = AtlasVaultKeyEpochRing.fromLegacy(vaultKey);

    expect(
      await migrated.deriveRecordKey(
        keyEpoch: 1,
        vaultId: 'legacy-vault',
        recordId: 'legacy-record',
      ),
      await deriveAtlasVaultRecordKey(
        vaultKey: vaultKey,
        vaultId: 'legacy-vault',
        recordId: 'legacy-record',
      ),
    );
  });

  test('legacy migration rejects a non-initial epoch', () async {
    final vaultKey = await _seed('legacy-record-key');

    expect(
      () => AtlasVaultKeyEpochRing.fromLegacy(vaultKey, keyEpoch: 2),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
  });

  test('epoch delivery open requires a trusted monotonic floor', () {
    final source = File(
      'lib/src/atlas_vault/key_epochs.dart',
    ).readAsStringSync();

    expect(source, contains('required int minimumKeyEpoch'));
    expect(source, isNot(contains('int minimumKeyEpoch = 1')));
  });

  test('current epoch HPKE matches vector and rejects epoch tamper', () async {
    final vector = atlasVaultObject(root['hpke_v2_epoch_delivery']);
    final ring = await _ring(root);
    final sealed = await sealAtlasVaultCurrentEpochHPKEV2ForTesting(
      ring: ring,
      recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
      context: _bytes(vector, 'context_hex'),
      ephemeralPrivateKey: await _seed(vector['sender_seed_label']! as String),
    );

    expect(sealed.keyEpoch, vector['key_epoch']);
    expect(sealed.encapsulatedKey, _bytes(vector, 'encapsulated_key_hex'));
    expect(sealed.ciphertext, _bytes(vector, 'ciphertext_hex'));
    final opened = await openAtlasVaultEpochHPKEV2(
      recipientPrivateKey: await _seed(
        vector['recipient_seed_label']! as String,
      ),
      sealed: sealed,
      context: _bytes(vector, 'context_hex'),
      minimumKeyEpoch: 3,
    );
    expect(opened.keyEpoch, 3);
    expect(opened.vaultKey, ring.currentVaultKey);

    await expectLater(
      openAtlasVaultEpochHPKEV2(
        recipientPrivateKey: await _seed(
          vector['recipient_seed_label']! as String,
        ),
        sealed: AtlasVaultEpochHPKESealedVaultKeyV2(
          keyEpoch: 2,
          encapsulatedKey: sealed.encapsulatedKey,
          ciphertext: sealed.ciphertext,
        ),
        context: _bytes(vector, 'context_hex'),
        minimumKeyEpoch: 1,
      ),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
  });

  test(
    'only the current epoch is writable and stale delivery is rejected',
    () async {
      final vector = atlasVaultObject(root['hpke_v2_epoch_delivery']);
      final ring = await _ring(root);
      final sealed = await ring.sealCurrentHPKEV2(
        recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
        context: _bytes(vector, 'context_hex'),
      );

      expect(sealed.keyEpoch, 3);
      await expectLater(
        openAtlasVaultEpochHPKEV2(
          recipientPrivateKey: await _seed(
            vector['recipient_seed_label']! as String,
          ),
          sealed: sealed,
          context: _bytes(vector, 'context_hex'),
          minimumKeyEpoch: 4,
        ),
        throwsA(isA<AtlasVaultKeyEpochException>()),
      );
    },
  );

  test('epoch ring rejects ambiguous or unbounded state', () async {
    final keys = <int, Uint8List>{
      for (var epoch = 1; epoch <= 33; epoch += 1)
        epoch: await _seed('epoch-$epoch'),
    };
    expect(
      () => AtlasVaultKeyEpochRing(
        metadata: AtlasVaultKeyRingMetadata.fromJson(
          atlasVaultObject(atlasVaultObject(root['ring'])['metadata']),
        ),
        keys: <int, Uint8List>{1: keys[1]!, 3: keys[3]!},
      ),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
    expect(
      () => AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: 3,
        keys: <int, Uint8List>{1: keys[1]!, 2: keys[1]!, 3: keys[3]!},
      ),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
    expect(
      () => AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: atlasVaultMaximumKeyRingEntries + 1,
        keys: keys,
      ),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
  });

  test('epoch metadata rejects non-integer JSON numbers', () {
    final base = <String, Object?>{
      'format': 'atlasvault-vault-key-ring',
      'version': 1,
      'current_key_epoch': 3,
      'retained_key_epochs': <Object?>[1, 2],
    };
    final malformed = <Map<String, Object?>>[
      <String, Object?>{...base, 'version': true},
      <String, Object?>{...base, 'version': 1.0},
      <String, Object?>{
        ...base,
        'current_key_epoch': true,
        'retained_key_epochs': <Object?>[],
      },
      <String, Object?>{...base, 'current_key_epoch': 3.0},
      <String, Object?>{
        ...base,
        'retained_key_epochs': <Object?>[1, 2.0],
      },
    ];
    for (final value in malformed) {
      expect(
        () => AtlasVaultKeyRingMetadata.fromJson(value),
        throwsA(isA<AtlasVaultKeyEpochException>()),
      );
    }
  });

  test('epoch ring properties hold across bounded sizes', () async {
    for (
      var currentEpoch = 1;
      currentEpoch <= atlasVaultMaximumKeyRingEntries;
      currentEpoch += 1
    ) {
      final keys = <int, Uint8List>{
        for (var epoch = 1; epoch <= currentEpoch; epoch += 1)
          epoch: await _seed('property-$currentEpoch-$epoch'),
      };
      final ring = AtlasVaultKeyEpochRing.fromEntries(
        currentKeyEpoch: currentEpoch,
        keys: keys,
      );
      expect(ring.metadata.retainedKeyEpochs, <int>[
        for (var epoch = 1; epoch < currentEpoch; epoch += 1) epoch,
      ]);
      expect(ring.currentVaultKey, keys[currentEpoch]);
      final derived = <String>{};
      for (final epoch in keys.keys) {
        derived.add(
          _hex(
            await ring.deriveRecordKey(
              keyEpoch: epoch,
              vaultId: 'vault-$currentEpoch',
              recordId: 'record-$epoch',
            ),
          ),
        );
      }
      expect(derived, hasLength(keys.length));
    }
  });

  test('epoch metadata and delivery tamper fail closed', () async {
    final base = <String, Object?>{
      'format': 'atlasvault-vault-key-ring',
      'version': 1,
      'current_key_epoch': 3,
      'retained_key_epochs': <Object?>[1, 2],
    };
    final malformed = <Map<String, Object?>>[
      <String, Object?>{...base}..remove('format'),
      <String, Object?>{...base, 'unexpected': 1},
      <String, Object?>{
        ...base,
        'retained_key_epochs': <Object?>[2, 1],
      },
      <String, Object?>{
        ...base,
        'retained_key_epochs': <Object?>[1, 1],
      },
      <String, Object?>{
        ...base,
        'retained_key_epochs': <Object?>[1, 3],
      },
    ];
    for (final value in malformed) {
      expect(
        () => AtlasVaultKeyRingMetadata.fromJson(value),
        throwsA(isA<AtlasVaultKeyEpochException>()),
      );
    }

    final vector = atlasVaultObject(root['hpke_v2_epoch_delivery']);
    final ring = await _ring(root);
    final sealed = await sealAtlasVaultCurrentEpochHPKEV2ForTesting(
      ring: ring,
      recipientPublicKey: _bytes(vector, 'recipient_public_key_hex'),
      context: _bytes(vector, 'context_hex'),
      ephemeralPrivateKey: await _seed(vector['sender_seed_label']! as String),
    );
    final recipient = await _seed(vector['recipient_seed_label']! as String);
    final context = _bytes(vector, 'context_hex');
    final encapsulated = Uint8List.fromList(sealed.encapsulatedKey)..[0] ^= 1;
    final ciphertext = Uint8List.fromList(sealed.ciphertext)..last ^= 1;
    final variants = <AtlasVaultEpochHPKESealedVaultKeyV2>[
      AtlasVaultEpochHPKESealedVaultKeyV2(
        keyEpoch: sealed.keyEpoch,
        encapsulatedKey: encapsulated,
        ciphertext: sealed.ciphertext,
      ),
      AtlasVaultEpochHPKESealedVaultKeyV2(
        keyEpoch: sealed.keyEpoch,
        encapsulatedKey: sealed.encapsulatedKey,
        ciphertext: ciphertext,
      ),
    ];
    for (final value in variants) {
      await expectLater(
        openAtlasVaultEpochHPKEV2(
          recipientPrivateKey: recipient,
          sealed: value,
          context: context,
          minimumKeyEpoch: sealed.keyEpoch,
        ),
        throwsA(isA<AtlasVaultKeyEpochException>()),
      );
    }
    await expectLater(
      openAtlasVaultEpochHPKEV2(
        recipientPrivateKey: await _seed('wrong-recipient'),
        sealed: sealed,
        context: context,
        minimumKeyEpoch: sealed.keyEpoch,
      ),
      throwsA(isA<AtlasVaultKeyEpochException>()),
    );
  });
}

Future<AtlasVaultKeyEpochRing> _ring(Map<String, Object?> root) async {
  final vector = atlasVaultObject(root['ring']);
  final entries = atlasVaultList(vector['entries']);
  return AtlasVaultKeyEpochRing(
    metadata: AtlasVaultKeyRingMetadata.fromJson(
      atlasVaultObject(vector['metadata']),
    ),
    keys: <int, Uint8List>{
      for (final rawEntry in entries)
        atlasVaultObject(rawEntry)['key_epoch']! as int: await _seed(
          atlasVaultObject(rawEntry)['vault_key_label']! as String,
        ),
    },
  );
}

Future<Uint8List> _seed(String label) async {
  final digest = await Sha256().hash(ascii.encode(label));
  return Uint8List.fromList(digest.bytes);
}

Future<String> _sha256Hex(List<int> value) async {
  final digest = await Sha256().hash(value);
  return _hex(digest.bytes);
}

Uint8List _bytes(Map<String, Object?> vector, String field) {
  final value = vector[field]! as String;
  return Uint8List.fromList(<int>[
    for (var index = 0; index < value.length; index += 2)
      int.parse(value.substring(index, index + 2), radix: 16),
  ]);
}

String _hex(List<int> value) {
  return value.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();
}
