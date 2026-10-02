import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:atlas/src/atlas_vault/epoch_rotation.dart';
import 'package:atlas/src/atlas_vault/payloads.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

import 'atlas_vault_vector_loader.dart';

Map<String, Object?> runtimeObject(Object? value) =>
    Map<String, Object?>.from(value! as Map);
List<Map<String, Object?>> runtimeRows(Object? value) =>
    (value! as List).map(runtimeObject).toList();
Uint8List runtimeTestKey(int value) =>
    Uint8List.fromList(List<int>.filled(32, value));

/// Only the existing synthetic activation/catch-up vectors are used here.
final class RuntimeFixture {
  RuntimeFixture({bool catchUp = false})
    : vector = catchUp
          ? runtimeObject(
              jsonDecode(
                File(
                  '${atlasVaultRepositoryRoot().path}/contracts/sync/test_vectors/atlasvault_epoch_catch_up_history_v2.json',
                ).readAsStringSync(),
              ),
            )
          : loadAtlasVaultVector('atlasvault_activation_v1.json'),
      isCatchUp = catchUp;

  final Map<String, Object?> vector;
  final bool isCatchUp;
  static const vaultID = 'vault-c26';
  static const collectionID = 'collection-c26';
  Map<String, Object?> get record => runtimeObject(vector['record']);
  Map<String, Object?> get proof => runtimeObject(record['proof']);
  Map<String, Object?> get initialView => runtimeObject(vector['initial_view']);
  List<Map<String, Object?>> get initialHistoryRegistry => runtimeRows(
    vector[isCatchUp ? 'initial_history_registry' : 'initial_registry'],
  );
  List<Map<String, Object?>> get initialRegistry =>
      runtimeRows(isCatchUp ? vector['initial_registry'] : proof['registry']);
  String deviceID(int index) =>
      (vector['device_ids']! as List)[index] as String;
  Future<SimpleKeyPair> signer([int index = 0]) =>
      Ed25519().newKeyPairFromSeed(runtimeTestKey(10 + index));

  AtlasVaultEpochVault owner(Directory directory, [int index = 0]) =>
      AtlasVaultEpochVault(
        directory,
        storageKey: runtimeTestKey(50 + index),
        deviceID: deviceID(index),
        registry: initialRegistry,
        accountID: initialView['account_id']! as String,
        vaultID: vaultID,
        keyEpoch: 3,
        stateRoot: initialView['root']! as String,
      );

  Future<AtlasVaultEpochVault> initialize(
    Directory directory, {
    int index = 0,
    bool activate = true,
  }) async {
    await directory.create(recursive: true);
    final history = AtlasVaultGuardedSyncState(
      file: File('${directory.path}/initial-history'),
      encryptionKey: runtimeTestKey(60 + index),
      accountId: initialView['account_id']! as String,
      vaultId: vaultID,
      collectionId: collectionID,
      keyEpoch: 3,
      trustedSigner: Uint8List.fromList(
        (await (await signer()).extractPublicKey()).bytes,
      ),
    );
    await history.initialize();
    await history.ingest(
      initialView,
      runtimeRows(
        vector[isCatchUp ? 'initial_history_registry' : 'initial_registry'],
      ),
      runtimeObject(vector['initial_collection']),
      base64Decode(vector['opaque_state_b64']! as String),
    );
    final result = owner(directory, index);
    await result.initialize({3: runtimeTestKey(30)}, history: history);
    if (activate) await accept(result, index);
    return result;
  }

  Future<bool> accept(AtlasVaultEpochVault owner, [int index = 0]) =>
      owner.acceptRotation(
        proof,
        acceptedRecord: record,
        agreementPrivateKey: runtimeTestKey(20 + index),
      );

  Future<List<Map<String, Object?>>> activeRegistry() async => runtimeRows(
    (await AtlasVaultEpochRotation.verify(
      proof,
      registry: initialRegistry,
      accountID: initialView['account_id']! as String,
      vaultID: vaultID,
      previousEpoch: 3,
      stateRoot: initialView['root']! as String,
    ))['registry'],
  );

  List<Map<String, Object?>> packets(int index) =>
      runtimeRows((vector['packets']! as List)[index]);
  Future<bool> catchUp(AtlasVaultEpochVault owner, [int index = 2]) =>
      owner.catchUp(
        packets(index),
        currentActivationID: vector['target_activation_id']! as String,
        agreementPrivateKey: runtimeTestKey(20 + index),
        historyUpdates: runtimeRows(vector['history_updates']),
      );

  @override
  String toString() => 'RuntimeFixture(<redacted>)';
}

Map<String, AtlasVaultPayloadEnvelope> runtimePayloads() =>
    runtimeObject(
      loadAtlasVaultVector('atlasvault_payload_vectors_v1.json')['payloads'],
    ).map(
      (name, value) => MapEntry(
        name,
        AtlasVaultPayloadEnvelope.fromJson(runtimeObject(value)),
      ),
    );

AtlasVaultPayloadEnvelope runtimeUpdated(AtlasVaultPayloadEnvelope value) =>
    AtlasVaultPayloadEnvelope.fromJson({
      ...value.toJson(),
      'client_updated_at': '2026-09-05T00:00:00Z',
    });

/// Recompute encryption and the epoch signature instead of corrupting bytes.
/// This isolates authoritative runtime-body validation after P6 authentication.
Future<Map<String, Object?>> runtimeSignedOperation(
  RuntimeFixture fixture,
  AtlasVaultEpochVault author,
  AtlasVaultPayloadEnvelope payload, {
  String objectID = 'opaque-runtime-object',
  String revision = '10000000-0000-4000-8000-000000000001',
  String? parentRevision,
  int authorSequence = 1,
  Map<String, Object?> bodyChanges = const {},
}) async {
  final metadata = <String, Object?>{
    'operation_id':
        '10000000-0000-4000-8000-${authorSequence.toString().padLeft(12, '0')}',
    'author_device_id': fixture.deviceID(0),
    'author_sequence': authorSequence,
    'lamport': authorSequence,
    'object_id': objectID,
    'revision': revision,
    'parent_revision': parentRevision,
    'tombstone': false,
  };
  final bytes = Uint8List.fromList(
    utf8.encode(
      jsonEncode({
        'format': 'atlasvault-runtime-record',
        'version': 1,
        ...metadata,
        'payload': payload.toJson(),
        ...bodyChanges,
      }),
    ),
  );
  try {
    final sealed = await author.seal(
      'patch',
      bytes,
      objectID: objectID,
      revision: revision,
      signingKey: await fixture.signer(),
    );
    return {
      'format': 'atlasvault-encrypted-patch-operation',
      'version': 1,
      for (final key in [
        'operation_id',
        'author_device_id',
        'author_sequence',
        'lamport',
      ])
        key: metadata[key],
      'operation_type': 'upsert',
      'envelope': {...sealed.toJson(), 'parent_revision': parentRevision},
    };
  } finally {
    bytes.fillRange(0, bytes.length, 0);
  }
}

/// Real P6 signatures bind the exact guarded collection, root, registry and chain.
/// Full signed operation history travels separately and must reproduce winners.
/// No backend acceptance or native secure-storage proof is implied.
final class RuntimeSignedPage {
  const RuntimeSignedPage(
    this.view,
    this.registry,
    this.collection,
    this.bytes,
    this.operations,
  );
  final Map<String, Object?> view;
  final List<Map<String, Object?>> registry;
  final Map<String, Object?> collection;
  final Uint8List bytes;
  final List<Map<String, Object?>> operations;

  factory RuntimeSignedPage.fromPublication(
    Map<String, Object?> publication, {
    required List<Map<String, Object?>> registry,
  }) => RuntimeSignedPage(
    runtimeObject(publication['view']),
    registry,
    runtimeObject(publication['collection']),
    base64Decode(publication['opaque_state_b64']! as String),
    runtimeRows(publication['operations']),
  );

  static Future<RuntimeSignedPage> sign(
    RuntimeFixture fixture,
    List<Map<String, Object?>> operations, {
    Map<String, Object?>? previous,
  }) async {
    final prior = previous ?? fixture.initialView;
    // The public P5 reducer is durable-only. Use a disposable real replica so
    // the fixture does not implement its own ordering or tombstone semantics.
    final scratch = await Directory.systemTemp.createTemp(
      'atlas-runtime-page-',
    );
    late Uint8List bytes;
    try {
      final replica = AtlasVaultDurableEncryptedConvergentReplica(
        File('${scratch.path}/replica'),
        encryptionKey: runtimeTestKey(81),
        authenticationKey: runtimeTestKey(82),
        collectionId: RuntimeFixture.collectionID,
      );
      for (final operation in operations) {
        await replica.ingestRemote(
          AtlasVaultEncryptedPatchOperation.fromJson(operation),
        );
      }
      bytes = Uint8List.fromList(
        utf8.encode(
          jsonEncode({
            'format': 'atlasvault-guarded-collection',
            'version': 1,
            'route': 'patch',
            'records': (await replica.currentRecords())
                .map((record) => record.toJson())
                .toList(),
          }),
        ),
      );
    } finally {
      await scratch.delete(recursive: true);
    }
    final key = await fixture.signer();
    final registry = await fixture.activeRegistry();
    final collection = await AtlasVaultSignedStateCommitment.sign(
      bytes,
      collectionId: RuntimeFixture.collectionID,
      sequence: (prior['sequence']! as int) + 1,
      previousRoot: prior['collection_root']! as String,
      signingKey: key,
    );
    final view = await AtlasVaultAuthenticatedStateView.sign({
      'format': 'atlasvault-authenticated-state-view',
      'version': 2,
      'account_id': fixture.initialView['account_id'],
      'vault_id': RuntimeFixture.vaultID,
      'sequence': collection.sequence,
      'previous_root': prior['root'],
      'collection_root': collection.root,
      'registry_root': AtlasVaultRevocation.registryRoot(registry),
      'previous_registry_root': prior['registry_root'],
      'key_epoch': 4,
    }, key);
    return RuntimeSignedPage(
      view,
      registry,
      collection.toJson(),
      bytes,
      operations,
    );
  }

  Future<int> ingest(AtlasVaultEpochVault owner) => owner.ingestRuntimePage(
    view: view,
    registry: registry,
    collection: collection,
    opaqueState: bytes,
    operations: operations,
  );
  @override
  String toString() => 'RuntimeSignedPage(<redacted>)';
}
