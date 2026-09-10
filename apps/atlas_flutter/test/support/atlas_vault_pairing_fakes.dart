import 'dart:convert';
import 'dart:io';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/atlas_vault_android.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'atlas_vault_vector_loader.dart';
import 'atlas_vault_runtime_fixtures.dart';

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
  Future<void> Function(AtlasVaultPairingArtifact artifact)? beforeSave;

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
    await beforeSave?.call(artifact);
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
  const vaultId = RuntimeFixture.vaultID;
  final nativeIsInviter = platformRole == AtlasVaultPairingRole.inviter;
  final nativeSecret = await _secureJourneyIdentity(nativeIsInviter);
  final peerSecret = await _secureJourneyIdentity(!nativeIsInviter);
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
  var existingReplay = await platformStores.replay.read();
  // A completed previous role leaves an empty test replay ledger. Only the
  // other fixed synthetic role may be rebound; unrelated/native state is refused.
  if (existingReplay != null &&
      existingReplay.entries.isEmpty &&
      existingReplay.localDeviceId != nativeDeviceId &&
      !await platformStores.identity.containsPrimaryIdentity()) {
    final previous = AtlasVaultDeviceIdentitySecret.fromJson(
      atlasVaultObject(jsonDecode(utf8.decode(peerSecret))),
    );
    try {
      final previousIdentity = await previous.loadIdentity();
      try {
        if (existingReplay.localDeviceId == previousIdentity.deviceId) {
          final updated = AtlasVaultPairingReplayStore.fromJson({
            ...existingReplay.toJson(),
            'local_device_id': nativeDeviceId,
            'parent_revision': existingReplay.revision,
            'revision': _nextCleanupRevision(
              existingReplay.revision,
              existingReplay.parentRevision,
              replay: true,
            ),
          });
          await platformStores.replay.replace(
            updated,
            expectedSha256: await atlasVaultSha256Hex(
              existingReplay.canonicalBytes(),
            ),
          );
          existingReplay = await platformStores.replay.read();
        }
      } finally {
        previousIdentity.destroy();
      }
    } finally {
      previous.destroy();
    }
  }
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

  final root = await Directory.systemTemp.createTemp('c30-platform-journey-');
  final nativeSlots = <String>{};
  final nativeBinding = AtlasVaultRuntimeBinding(
    root: Directory('${root.path}/native'),
    loadKey: platformStores.secureKey.loadVaultKey,
    createKey: (id, key) async {
      await platformStores.secureKey.createVaultKey(id, key);
      nativeSlots.add(id);
    },
  );
  final peerBinding = AtlasVaultRuntimeBinding(
    root: Directory('${root.path}/peer'),
    loadKey: peerKeys.loadVaultKey,
    createKey: peerKeys.createVaultKey,
  );

  final platformRuntime = AtlasVaultPrivateStateRuntime(
    secureKeyStore: platformStores.secureKey,
    localStoreIO: platformStores.localStore,
    epochSessionFactory: nativeBinding.open,
    epochEnrollmentInstaller: nativeBinding.installEnrollment,
  );
  final peerRuntime = AtlasVaultPrivateStateRuntime(
    secureKeyStore: peerKeys,
    localStoreIO: peerLocal,
    epochSessionFactory: peerBinding.open,
    epochEnrollmentInstaller: peerBinding.installEnrollment,
  );
  final inviterBinding = nativeIsInviter ? nativeBinding : peerBinding;
  final fixture = RuntimeFixture();
  final owner = await fixture.initialize(inviterBinding.directory(vaultId));
  final payload = runtimePayloads().values.first;
  await owner.commitRuntimeRecord(
    payload: payload,
    objectID: 'platform-live',
    signingKey: await fixture.signer(),
  );
  final deleted = await owner.commitRuntimeRecord(
    payload: payload,
    objectID: 'platform-deleted',
    signingKey: await fixture.signer(),
  );
  await owner.commitRuntimeRecord(
    payload: null,
    objectID: 'platform-deleted',
    expectedRevision: deleted.envelope.revision,
    signingKey: await fixture.signer(),
  );
  await inviterBinding.provision(owner: owner, signingSeed: runtimeTestKey(10));
  if (nativeIsInviter) {
    await platformStores.selectedVault.create(vaultId);
    expect(
      await platformRuntime.activateExisting(vaultId),
      AtlasVaultActivationResult.activated,
    );
  } else {
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
    authorizeKeyRelease: (_) async => true,
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
    authorizeKeyRelease: (_) async => true,
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
    expect(acceptance.sas == null, isTrue);
    final inviteeCodes = await invitee.savePairingAcceptance();
    expect(
      inviteeCodes.disposition,
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
    expect(inviterCodes.sas != null, isTrue);
    expect(inviterCodes.sas == inviteeCodes.sas, isTrue);
    expect(
      (await inviter.confirmCodesMatch(
        expectedTranscriptSha256: inviterCodes.transcriptSha256,
      )).disposition,
      AtlasVaultTrustedPairingDisposition.deliveryReady,
    );
    expect(
      (await invitee.confirmCodesMatch(
        expectedTranscriptSha256: inviteeCodes.transcriptSha256,
      )).disposition,
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
    expect(nativeRegistry, isNull);
    expect(nativeReplay?.entries, hasLength(1));
    expect(await platformStores.transaction.read(), isNull);
    for (final kind in AtlasVaultPairingArtifactKind.values) {
      expect(await platformStores.staging.read(kind), isNull);
    }
    expect(await platformStores.selectedVault.read(), vaultId);
    expect(await platformStores.localStore.read(vaultId), isNull);
    final reopened = await nativeBinding.open(vaultId);
    final nativeRecords = await reopened.read();
    expect(nativeRecords, hasLength(2));
    expect(
      nativeRecords.where((record) => record.payload == null),
      hasLength(1),
    );
    final registry = await reopened.owner.enrollmentRegistry();
    expect(
      registry.where(
        (row) => row['device_id'] == nativeDeviceId && row['state'] == 'ACTIVE',
      ),
      hasLength(1),
    );
    reopened.close();
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
      expect(text.contains(sentinel), isFalse);
      expect(text.contains('"vault_key"'), isFalse);
      expect(text.contains('"private_key"'), isFalse);
      expect(text.contains(inviterCodes.sas!), isFalse);
      expect(text.contains('"sas"'), isFalse);
    }

    final evidence = AtlasVaultPairingPlatformJourneyEvidence(
      role: platformRole,
      vaultId: vaultId,
      sas: inviterCodes.sas!,
      artifacts: artifacts,
      installedRecordCount: nativeRecords.length,
      tombstoneCount: nativeRecords
          .where((record) => record.payload == null)
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
    for (final id in nativeSlots) {
      await platformStores.secureKey.deleteVaultKey(id);
      expect(await platformStores.secureKey.containsVaultKey(id), isFalse);
    }
    await platformStores.identity.deletePrimaryIdentity();
    for (final key in peerKeys.values.values) {
      key.fillRange(0, key.length, 0);
    }
    await root.delete(recursive: true);
    nativeSecret.fillRange(0, nativeSecret.length, 0);
    peerSecret.fillRange(0, peerSecret.length, 0);
  }
}

Future<Uint8List> _secureJourneyIdentity(bool inviter) async {
  final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
    signingPrivateSeed: runtimeTestKey(inviter ? 10 : 90),
    agreementPrivateKey: runtimeTestKey(inviter ? 20 : 100),
    createdAt: '2026-08-15T10:00:00Z',
    keyEpoch: 1,
  );
  final secret = identity.secretBundle();
  try {
    return secret.canonicalBytes();
  } finally {
    secret.destroy();
    identity.destroy();
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
  if (registry != null) {
    expect((await stores.registry.read())?.localDeviceId, localDeviceId);
    expect((await stores.registry.read())?.devices, isEmpty);
  } else {
    expect(await stores.registry.read(), isNull);
  }
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
