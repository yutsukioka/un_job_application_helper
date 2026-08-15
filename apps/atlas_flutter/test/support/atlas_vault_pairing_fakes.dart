import 'dart:convert';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'atlas_vault_vector_loader.dart';

typedef AtlasVaultPairingMethodHandler =
    Future<Object?> Function(MethodCall call);

final class AtlasVaultPairingMethodCallRecorder {
  AtlasVaultPairingMethodCallRecorder({required this.channelName})
    : channel = MethodChannel(channelName);

  final String channelName;
  final MethodChannel channel;
  final List<MethodCall> calls = <MethodCall>[];
  AtlasVaultPairingMethodHandler? handler;

  void install() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          calls.add(call);
          return handler?.call(call);
        });
  }

  void uninstall() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  }
}

final class AtlasVaultPairingMemoryIdentityStore
    implements AtlasDeviceIdentitySecretStore {
  AtlasVaultPairingMemoryIdentityStore([Uint8List? initial])
    : _value = initial == null ? null : Uint8List.fromList(initial);

  Uint8List? _value;
  int createCalls = 0;
  int loadCalls = 0;

  @override
  Future<void> createPrimaryIdentity(Uint8List canonicalSecretBundle) async {
    createCalls += 1;
    if (_value != null) throw StateError('identity exists');
    _value = Uint8List.fromList(canonicalSecretBundle);
  }

  @override
  Future<Uint8List?> loadPrimaryIdentity() async {
    loadCalls += 1;
    return _value == null ? null : Uint8List.fromList(_value!);
  }

  @override
  Future<bool> containsPrimaryIdentity() async => _value != null;

  @override
  Future<void> deletePrimaryIdentity() async => _value = null;
}

final class AtlasVaultPairingMemoryRegistryStore
    implements AtlasVaultTrustedDeviceRegistryStore {
  AtlasVaultTrustedDeviceRegistry? value;
  final List<String> events;

  AtlasVaultPairingMemoryRegistryStore({List<String>? events})
    : events = events ?? <String>[];

  @override
  Future<AtlasVaultTrustedDeviceRegistry?> read() async => value == null
      ? null
      : AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(
          value!.canonicalBytes(),
        );

  @override
  Future<void> create(AtlasVaultTrustedDeviceRegistry registry) async {
    if (value != null) throw StateError('registry exists');
    events.add('registry.create');
    value = AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(
      registry.canonicalBytes(),
    );
  }

  @override
  Future<void> replace(
    AtlasVaultTrustedDeviceRegistry registry, {
    required String expectedSha256,
  }) async {
    final current = value;
    if (current == null ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw StateError('registry CAS');
    }
    events.add('registry.replace');
    value = AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(
      registry.canonicalBytes(),
    );
  }
}

final class AtlasVaultPairingMemoryReplayStore
    implements AtlasVaultPairingReplayStateStore {
  AtlasVaultPairingReplayStore? value;
  final List<String> events;

  AtlasVaultPairingMemoryReplayStore({List<String>? events})
    : events = events ?? <String>[];

  @override
  Future<AtlasVaultPairingReplayStore?> read() async => value == null
      ? null
      : AtlasVaultPairingReplayStore.fromCanonicalBytes(
          value!.canonicalBytes(),
        );

  @override
  Future<void> create(AtlasVaultPairingReplayStore replayStore) async {
    if (value != null) throw StateError('replay exists');
    events.add('replay.create');
    value = AtlasVaultPairingReplayStore.fromCanonicalBytes(
      replayStore.canonicalBytes(),
    );
  }

  @override
  Future<void> replace(
    AtlasVaultPairingReplayStore replayStore, {
    required String expectedSha256,
  }) async {
    final current = value;
    if (current == null ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw StateError('replay CAS');
    }
    events.add('replay.replace');
    value = AtlasVaultPairingReplayStore.fromCanonicalBytes(
      replayStore.canonicalBytes(),
    );
  }
}

final class AtlasVaultPairingMemoryTransactionStore
    implements AtlasVaultPairingTransactionStore {
  AtlasVaultPairingTransaction? value;
  final List<String> events;
  final AtlasVaultPairingStage? failReplaceStage;
  int failReplaceCount;
  int failDeleteCount;

  AtlasVaultPairingMemoryTransactionStore({
    List<String>? events,
    this.failReplaceStage,
    this.failReplaceCount = 0,
    this.failDeleteCount = 0,
  }) : events = events ?? <String>[];

  @override
  Future<AtlasVaultPairingTransaction?> read() async => value == null
      ? null
      : AtlasVaultPairingTransaction.fromCanonicalBytes(
          value!.canonicalBytes(),
        );

  @override
  Future<void> create(AtlasVaultPairingTransaction transaction) async {
    if (value != null) throw StateError('transaction exists');
    events.add('transaction.create:${transaction.stage.encoded}');
    value = AtlasVaultPairingTransaction.fromCanonicalBytes(
      transaction.canonicalBytes(),
    );
  }

  @override
  Future<void> replace(
    AtlasVaultPairingTransaction transaction, {
    required String expectedSha256,
  }) async {
    if (transaction.stage == failReplaceStage && failReplaceCount > 0) {
      failReplaceCount -= 1;
      throw StateError('injected transaction replace failure');
    }
    final current = value;
    if (current == null ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw StateError('transaction CAS');
    }
    events.add('transaction.replace:${transaction.stage.encoded}');
    value = AtlasVaultPairingTransaction.fromCanonicalBytes(
      transaction.canonicalBytes(),
    );
  }

  @override
  Future<void> delete({required String expectedSha256}) async {
    if (failDeleteCount > 0) {
      failDeleteCount -= 1;
      throw StateError('injected transaction delete failure');
    }
    final current = value;
    if (current == null ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw StateError('transaction delete');
    }
    events.add('transaction.delete');
    value = null;
  }
}

final class AtlasVaultPairingMemoryStageStore
    implements AtlasVaultPairingArtifactStageStore {
  final Map<AtlasVaultPairingArtifactKind, Uint8List> values =
      <AtlasVaultPairingArtifactKind, Uint8List>{};
  final List<String> events;
  final AtlasVaultPairingArtifactKind? failCreateKind;

  AtlasVaultPairingMemoryStageStore({List<String>? events, this.failCreateKind})
    : events = events ?? <String>[];

  @override
  Future<AtlasVaultPairingArtifact?> read(
    AtlasVaultPairingArtifactKind kind,
  ) async => values[kind] == null
      ? null
      : AtlasVaultPairingArtifact.fromCanonicalBytes(values[kind]!);

  @override
  Future<void> create(AtlasVaultPairingArtifact artifact) async {
    if (artifact.kind == failCreateKind) {
      throw StateError('injected stage create failure');
    }
    if (values.containsKey(artifact.kind)) throw StateError('stage exists');
    events.add('stage.create:${artifact.kind.encoded}');
    values[artifact.kind] = Uint8List.fromList(artifact.canonicalBytes());
  }

  @override
  Future<void> delete(
    AtlasVaultPairingArtifactKind kind, {
    required String expectedSha256,
  }) async {
    final bytes = values[kind];
    if (bytes == null || await atlasVaultSha256Hex(bytes) != expectedSha256) {
      throw StateError('stage delete');
    }
    events.add('stage.delete:${kind.encoded}');
    values.remove(kind);
  }
}

final class AtlasVaultPairingMailbox {
  Uint8List? bytes;
}

final class AtlasVaultPairingMemoryTransport
    implements AtlasVaultPairingArtifactTransport {
  AtlasVaultPairingMemoryTransport(this.mailbox, {List<String>? events})
    : events = events ?? <String>[];

  final AtlasVaultPairingMailbox mailbox;
  final List<String> events;
  bool cancelNextPick = false;
  bool cancelNextSave = false;

  @override
  Future<AtlasVaultPairingArtifact?> pick() async {
    if (cancelNextPick) {
      cancelNextPick = false;
      return null;
    }
    final value = mailbox.bytes;
    if (value == null) return null;
    mailbox.bytes = null;
    final artifact = AtlasVaultPairingArtifact.fromCanonicalBytes(value);
    events.add('transport.pick:${artifact.kind.encoded}');
    return artifact;
  }

  @override
  Future<bool> save(AtlasVaultPairingArtifact artifact) async {
    if (cancelNextSave) {
      cancelNextSave = false;
      return false;
    }
    if (mailbox.bytes != null) throw StateError('mailbox occupied');
    mailbox.bytes = Uint8List.fromList(artifact.canonicalBytes());
    events.add('transport.save:${artifact.kind.encoded}');
    return true;
  }
}

final class AtlasVaultPairingMemorySecureKeyStore
    implements AtlasVaultSecureKeyStore {
  final Map<String, Uint8List> values = <String, Uint8List>{};
  final List<String> events;

  AtlasVaultPairingMemorySecureKeyStore({List<String>? events})
    : events = events ?? <String>[];

  @override
  Future<void> createVaultKey(String vaultId, Uint8List vaultKey) async {
    if (values.containsKey(vaultId)) throw StateError('key exists');
    events.add('key.create');
    values[vaultId] = Uint8List.fromList(vaultKey);
  }

  @override
  Future<Uint8List?> loadVaultKey(String vaultId) async =>
      values[vaultId] == null ? null : Uint8List.fromList(values[vaultId]!);

  @override
  Future<bool> containsVaultKey(String vaultId) async =>
      values.containsKey(vaultId);

  @override
  Future<void> deleteVaultKey(String vaultId) async {
    events.add('key.delete');
    values.remove(vaultId);
  }
}

final class AtlasVaultPairingMemoryLocalStore
    implements AtlasVaultLocalStoreIO {
  final Map<String, AtlasVaultLocalStore> values =
      <String, AtlasVaultLocalStore>{};
  final List<String> events;

  AtlasVaultPairingMemoryLocalStore({List<String>? events})
    : events = events ?? <String>[];

  @override
  Future<AtlasVaultLocalStore?> read(String vaultId) async => values[vaultId];

  @override
  Future<void> create(String vaultId, AtlasVaultLocalStore store) async {
    if (values.containsKey(vaultId)) throw StateError('store exists');
    events.add('store.create');
    values[vaultId] = AtlasVaultLocalStore.decodeJson(
      String.fromCharCodes(store.canonicalBytes()),
    );
  }

  @override
  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  }) async {
    final current = values[vaultId];
    if (current == null ||
        await atlasVaultSha256Hex(current.canonicalBytes()) != expectedSha256) {
      throw StateError('store CAS');
    }
    values[vaultId] = store;
  }

  @override
  Future<void> delete(String vaultId) async {
    events.add('store.delete');
    values.remove(vaultId);
  }
}

final class AtlasVaultPairingMemorySelectedVaultStore
    implements AtlasVaultSelectedVaultStore {
  String? value;
  final List<String> events;

  AtlasVaultPairingMemorySelectedVaultStore({List<String>? events})
    : events = events ?? <String>[];

  @override
  Future<String?> read() async => value;

  @override
  Future<void> create(String vaultId) async {
    if (value != null) throw StateError('selection exists');
    events.add('selection.create');
    value = vaultId;
  }

  @override
  Future<void> clear(String expectedVaultId) async {
    if (value != expectedVaultId) throw StateError('selection mismatch');
    value = null;
  }
}

final class AtlasVaultPairingDeterminism {
  AtlasVaultPairingDeterminism({this.seed = 1});

  int seed;

  String uuid() {
    final current = seed++;
    return '52000000-0000-4000-8000-${current.toString().padLeft(12, '0')}';
  }

  Uint8List bytes(int length) {
    final marker = (seed++ % 250) + 1;
    return Uint8List.fromList(List<int>.filled(length, marker));
  }
}

final class AtlasVaultPairingPlatformStores {
  const AtlasVaultPairingPlatformStores({
    required this.identity,
    required this.registry,
    required this.replay,
    required this.transaction,
    required this.staging,
    required this.secureKey,
    required this.localStore,
    required this.selectedVault,
  });

  final AtlasDeviceIdentitySecretStore identity;
  final AtlasVaultTrustedDeviceRegistryStore registry;
  final AtlasVaultPairingReplayStateStore replay;
  final AtlasVaultPairingTransactionStore transaction;
  final AtlasVaultPairingArtifactStageStore staging;
  final AtlasVaultSecureKeyStore secureKey;
  final AtlasVaultLocalStoreIO localStore;
  final AtlasVaultSelectedVaultStore selectedVault;
}

final class AtlasVaultPairingPlatformJourneyEvidence {
  AtlasVaultPairingPlatformJourneyEvidence({
    required this.role,
    required this.vaultId,
    required this.sas,
    required Map<AtlasVaultPairingArtifactKind, Uint8List> artifacts,
    required this.installedRecordCount,
    required this.tombstoneCount,
  }) : artifacts = <AtlasVaultPairingArtifactKind, Uint8List>{
         for (final entry in artifacts.entries)
           entry.key: Uint8List.fromList(entry.value),
       };

  final AtlasVaultPairingRole role;
  final String vaultId;
  final String sas;
  final Map<AtlasVaultPairingArtifactKind, Uint8List> artifacts;
  final int installedRecordCount;
  final int tombstoneCount;
}

Future<AtlasVaultPairingPlatformJourneyEvidence>
runAtlasVaultPairingPlatformJourney({
  required Map<String, Object?> vector,
  required AtlasVaultPairingRole platformRole,
  required AtlasVaultPairingPlatformStores platformStores,
}) async {
  final bootstrap = AtlasVaultPairingBootstrap.fromJson(
    atlasVaultObject(vector['bootstrap']),
  );
  final vaultId = bootstrap.vaultMetadata.vaultId;
  final rawVaultKey = Uint8List.fromList(
    base64Decode(vector['test_only_vault_key_b64']! as String),
  );
  final nativeSecret = await atlasVaultPairingIdentitySecret(vector, 'inviter');
  final peerSecret = await atlasVaultPairingIdentitySecret(vector, 'invitee');
  final nativeIdentity = await AtlasVaultDeviceIdentitySecret.fromJson(
    atlasVaultObject(jsonDecode(utf8.decode(nativeSecret))),
  ).loadIdentity();
  final nativeDeviceId = nativeIdentity.deviceId;
  nativeIdentity.destroy();

  final existingRegistry = await platformStores.registry.read();
  if (existingRegistry != null &&
      (existingRegistry.localDeviceId != nativeDeviceId ||
          existingRegistry.devices.isNotEmpty)) {
    throw StateError('native pairing registry is not clean');
  }
  final existingReplay = await platformStores.replay.read();
  if (existingReplay != null &&
      (existingReplay.localDeviceId != nativeDeviceId ||
          existingReplay.entries.isNotEmpty)) {
    throw StateError('native pairing replay state is not clean');
  }
  if (await platformStores.transaction.read() != null ||
      await platformStores.selectedVault.read() != null ||
      await platformStores.secureKey.containsVaultKey(vaultId) ||
      await platformStores.localStore.read(vaultId) != null) {
    throw StateError('native pairing install state is not clean');
  }
  for (final kind in AtlasVaultPairingArtifactKind.values) {
    if (await platformStores.staging.read(kind) != null) {
      throw StateError('native pairing staging is not clean');
    }
  }

  if (await platformStores.identity.containsPrimaryIdentity()) {
    final restored = await platformStores.identity.loadPrimaryIdentity();
    if (restored == null || !_pairingBytesEqual(restored, nativeSecret)) {
      restored?.fillRange(0, restored.length, 0);
      throw StateError('native pairing identity is not the test identity');
    }
    restored.fillRange(0, restored.length, 0);
  } else {
    await platformStores.identity.createPrimaryIdentity(nativeSecret);
  }

  final peerEvents = <String>[];
  final peerIdentity = AtlasVaultPairingMemoryIdentityStore(peerSecret);
  final peerRegistry = AtlasVaultPairingMemoryRegistryStore(events: peerEvents);
  final peerReplay = AtlasVaultPairingMemoryReplayStore(events: peerEvents);
  final peerTransaction = AtlasVaultPairingMemoryTransactionStore(
    events: peerEvents,
  );
  final peerStaging = AtlasVaultPairingMemoryStageStore(events: peerEvents);
  final peerKeys = AtlasVaultPairingMemorySecureKeyStore(events: peerEvents);
  final peerLocal = AtlasVaultPairingMemoryLocalStore(events: peerEvents);
  final peerSelected = AtlasVaultPairingMemorySelectedVaultStore(
    events: peerEvents,
  );
  final mailbox = AtlasVaultPairingMailbox();
  final platformTransport = AtlasVaultPairingMemoryTransport(mailbox);
  final peerTransport = AtlasVaultPairingMemoryTransport(
    mailbox,
    events: peerEvents,
  );

  final platformRuntime = AtlasVaultPrivateStateRuntime(
    secureKeyStore: platformStores.secureKey,
    localStoreIO: platformStores.localStore,
  );
  final peerRuntime = AtlasVaultPrivateStateRuntime(
    secureKeyStore: peerKeys,
    localStoreIO: peerLocal,
  );
  final localStore = AtlasVaultLocalStore.fromJson(<String, Object?>{
    'format': AtlasVaultLocalStore.format,
    'version': AtlasVaultLocalStore.version,
    'store_id': '63000000-0000-4000-8000-000000000001',
    'created_at': '2026-08-15T10:00:00Z',
    'updated_at': '2026-08-15T10:00:00Z',
    'vault_metadata': bootstrap.vaultMetadata.toJson(),
    'records': <Object?>[
      for (final record in bootstrap.records) record.toJson(),
    ],
  });

  final nativeIsInviter = platformRole == AtlasVaultPairingRole.inviter;
  if (nativeIsInviter) {
    await platformStores.localStore.create(vaultId, localStore);
    await platformStores.secureKey.createVaultKey(vaultId, rawVaultKey);
    await platformStores.selectedVault.create(vaultId);
    expect(
      await platformRuntime.activateExisting(vaultId),
      AtlasVaultActivationResult.activated,
    );
  } else {
    peerLocal.values[vaultId] = localStore;
    peerKeys.values[vaultId] = Uint8List.fromList(rawVaultKey);
    peerSelected.value = vaultId;
    expect(
      await peerRuntime.activateExisting(vaultId),
      AtlasVaultActivationResult.activated,
    );
  }

  final platformDeterminism = AtlasVaultPairingDeterminism(seed: 50);
  final peerDeterminism = AtlasVaultPairingDeterminism(seed: 500);
  final clock = DateTime.utc(2026, 8, 15, 10, 5);
  final platform = AtlasVaultTrustedPairingCoordinator(
    identityStore: platformStores.identity,
    registryStore: platformStores.registry,
    replayStore: platformStores.replay,
    transactionStore: platformStores.transaction,
    stageStore: platformStores.staging,
    artifactTransport: platformTransport,
    runtime: platformRuntime,
    cleanInstallProbe: () async => nativeIsInviter
        ? AtlasVaultPairingCleanInstallDisposition.existingVault
        : AtlasVaultPairingCleanInstallDisposition.clean,
    secureKeyStore: platformStores.secureKey,
    localStoreIO: platformStores.localStore,
    selectedVaultStore: platformStores.selectedVault,
    uuidProvider: platformDeterminism.uuid,
    randomBytes: platformDeterminism.bytes,
    now: () => clock,
  );
  final peer = AtlasVaultTrustedPairingCoordinator(
    identityStore: peerIdentity,
    registryStore: peerRegistry,
    replayStore: peerReplay,
    transactionStore: peerTransaction,
    stageStore: peerStaging,
    artifactTransport: peerTransport,
    runtime: peerRuntime,
    cleanInstallProbe: () async => nativeIsInviter
        ? AtlasVaultPairingCleanInstallDisposition.clean
        : AtlasVaultPairingCleanInstallDisposition.existingVault,
    secureKeyStore: peerKeys,
    localStoreIO: peerLocal,
    selectedVaultStore: peerSelected,
    uuidProvider: peerDeterminism.uuid,
    randomBytes: peerDeterminism.bytes,
    now: () => clock,
  );
  final inviter = nativeIsInviter ? platform : peer;
  final invitee = nativeIsInviter ? peer : platform;
  final inviterStage = nativeIsInviter ? platformStores.staging : peerStaging;
  final inviteeStage = nativeIsInviter ? peerStaging : platformStores.staging;
  final artifacts = <AtlasVaultPairingArtifactKind, Uint8List>{};

  try {
    expect(
      (await inviter.createPairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.offerReady,
    );
    artifacts[AtlasVaultPairingArtifactKind.offer] = (await inviterStage.read(
      AtlasVaultPairingArtifactKind.offer,
    ))!.canonicalBytes();
    expect(
      (await inviter.savePairingOffer()).disposition,
      AtlasVaultTrustedPairingDisposition.offerSaved,
    );
    final acceptance = await invitee.importPairingOffer();
    expect(
      acceptance.disposition,
      AtlasVaultTrustedPairingDisposition.acceptanceReady,
    );
    expect(
      (await invitee.savePairingAcceptance()).disposition,
      AtlasVaultTrustedPairingDisposition.acceptanceSaved,
    );
    artifacts[AtlasVaultPairingArtifactKind.acceptance] =
        (await inviteeStage.read(
          AtlasVaultPairingArtifactKind.acceptance,
        ))!.canonicalBytes();
    final inviterCodes = await inviter.importPairingAcceptance();
    expect(
      inviterCodes.disposition,
      AtlasVaultTrustedPairingDisposition.codesReady,
    );
    expect(inviterCodes.sas, acceptance.sas);
    expect(
      (await inviter.confirmCodesMatch()).disposition,
      AtlasVaultTrustedPairingDisposition.deliveryReady,
    );
    expect(
      (await invitee.confirmCodesMatch()).disposition,
      AtlasVaultTrustedPairingDisposition.codesConfirmed,
    );
    artifacts[AtlasVaultPairingArtifactKind.delivery] =
        (await inviterStage.read(
          AtlasVaultPairingArtifactKind.delivery,
        ))!.canonicalBytes();
    expect(
      (await inviter.saveKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.deliverySaved,
    );
    expect(
      (await invitee.importKeyDelivery()).disposition,
      AtlasVaultTrustedPairingDisposition.acknowledgementReady,
    );
    artifacts[AtlasVaultPairingArtifactKind.acknowledgement] =
        (await inviteeStage.read(
          AtlasVaultPairingArtifactKind.acknowledgement,
        ))!.canonicalBytes();
    expect(
      (await invitee.savePairingAcknowledgement()).disposition,
      AtlasVaultTrustedPairingDisposition.completed,
    );
    expect(
      (await inviter.importPairingAcknowledgement()).disposition,
      AtlasVaultTrustedPairingDisposition.completed,
    );

    final nativeRegistry = await platformStores.registry.read();
    final nativeReplay = await platformStores.replay.read();
    expect(nativeRegistry?.devices, hasLength(1));
    expect(nativeReplay?.entries, hasLength(1));
    expect(await platformStores.transaction.read(), isNull);
    for (final kind in AtlasVaultPairingArtifactKind.values) {
      expect(await platformStores.staging.read(kind), isNull);
    }
    expect(await platformStores.selectedVault.read(), vaultId);
    final nativeStore = await platformStores.localStore.read(vaultId);
    expect(nativeStore?.records, hasLength(bootstrap.records.length));
    expect(
      nativeStore?.records.where((record) => record.deleted),
      hasLength(1),
    );
    final duplicate = consumeAtlasVaultPairingReplay(
      nativeReplay!,
      nativeReplay.entries.single,
      revision: '65000000-0000-4000-8000-000000000001',
      updatedAt: '2026-08-15T10:06:00Z',
      currentTime: '2026-08-15T10:06:00Z',
    );
    expect(duplicate.outcome, AtlasVaultReplayConsumeOutcome.alreadyConsumed);

    final sentinel =
        atlasVaultObject(
              vector['expected_payloads'],
            )['unsupported_private_sentinel']!
            as String;
    for (final bytes in artifacts.values) {
      final text = utf8.decode(bytes);
      expect(text, isNot(contains(sentinel)));
      expect(text, isNot(contains('"vault_key"')));
      expect(text, isNot(contains('"private_key"')));
    }

    final evidence = AtlasVaultPairingPlatformJourneyEvidence(
      role: platformRole,
      vaultId: vaultId,
      sas: inviterCodes.sas!,
      artifacts: artifacts,
      installedRecordCount: nativeStore!.records.length,
      tombstoneCount: nativeStore.records
          .where((record) => record.deleted)
          .length,
    );
    await _cleanNativePairingJourney(
      stores: platformStores,
      runtime: platformRuntime,
      vaultId: vaultId,
      localDeviceId: nativeDeviceId,
    );
    return evidence;
  } finally {
    await inviter.stop();
    await invitee.stop();
    await platformRuntime.deactivate();
    await peerRuntime.deactivate();
    rawVaultKey.fillRange(0, rawVaultKey.length, 0);
    nativeSecret.fillRange(0, nativeSecret.length, 0);
    peerSecret.fillRange(0, peerSecret.length, 0);
  }
}

Future<Uint8List> atlasVaultPairingIdentitySecret(
  Map<String, Object?> vector,
  String name,
) async {
  final data = atlasVaultObject(vector[name]);
  final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: Uint8List.fromList(
      base64Decode(data['signing_private_seed_b64']! as String),
    ),
    agreementPrivateKey: Uint8List.fromList(
      base64Decode(data['agreement_private_key_b64']! as String),
    ),
    createdAt: data['created_at']! as String,
    keyEpoch: data['key_epoch']! as int,
    expectedDeviceId: data['device_id']! as String,
  );
  final secret = identity.secretBundle();
  try {
    return secret.canonicalBytes();
  } finally {
    secret.destroy();
    identity.destroy();
  }
}

Future<void> _cleanNativePairingJourney({
  required AtlasVaultPairingPlatformStores stores,
  required AtlasVaultPrivateStateRuntime runtime,
  required String vaultId,
  required String localDeviceId,
}) async {
  await runtime.deactivate();
  if (await stores.selectedVault.read() == vaultId) {
    await stores.selectedVault.clear(vaultId);
  }
  if (await stores.secureKey.containsVaultKey(vaultId)) {
    await stores.secureKey.deleteVaultKey(vaultId);
  }
  if (await stores.localStore.read(vaultId) != null) {
    await stores.localStore.delete(vaultId);
  }
  final registry = await stores.registry.read();
  if (registry != null) {
    final revision = _nextCleanupRevision(
      registry.revision,
      registry.parentRevision,
      replay: false,
    );
    final replacement =
        AtlasVaultTrustedDeviceRegistry.fromJson(<String, Object?>{
          ...registry.toJson(),
          'revision': revision,
          'parent_revision': registry.revision,
          'updated_at': registry.updatedAt,
          'devices': const <Object?>[],
        });
    await stores.registry.replace(
      replacement,
      expectedSha256: await atlasVaultSha256Hex(registry.canonicalBytes()),
    );
  }
  final replay = await stores.replay.read();
  if (replay != null) {
    final revision = _nextCleanupRevision(
      replay.revision,
      replay.parentRevision,
      replay: true,
    );
    final replacement = AtlasVaultPairingReplayStore.fromJson(<String, Object?>{
      ...replay.toJson(),
      'revision': revision,
      'parent_revision': replay.revision,
      'updated_at': replay.updatedAt,
      'entries': const <Object?>[],
    });
    await stores.replay.replace(
      replacement,
      expectedSha256: await atlasVaultSha256Hex(replay.canonicalBytes()),
    );
  }
  await stores.identity.deletePrimaryIdentity();
  expect(await stores.selectedVault.read(), isNull);
  expect(await stores.secureKey.containsVaultKey(vaultId), isFalse);
  expect(await stores.localStore.read(vaultId), isNull);
  expect(await stores.identity.containsPrimaryIdentity(), isFalse);
  expect((await stores.registry.read())?.localDeviceId, localDeviceId);
  expect((await stores.registry.read())?.devices, isEmpty);
  expect((await stores.replay.read())?.localDeviceId, localDeviceId);
  expect((await stores.replay.read())?.entries, isEmpty);
}

bool _pairingBytesEqual(Uint8List left, Uint8List right) {
  if (left.length != right.length) return false;
  var difference = 0;
  for (var index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference == 0;
}

String _nextCleanupRevision(
  String current,
  String? parent, {
  required bool replay,
}) {
  final suffix = replay ? '000000000002' : '000000000001';
  for (final prefix in const <String>['66000000', '67000000', '68000000']) {
    final candidate = '$prefix-0000-4000-8000-$suffix';
    if (candidate != current && candidate != parent) return candidate;
  }
  throw StateError('native pairing cleanup revision is unavailable');
}
