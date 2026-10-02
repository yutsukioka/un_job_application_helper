import 'dart:io';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_pairing_fakes.dart';
import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_runtime_native_harness.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'C30 production epoch runtime can begin explicit device enrollment',
    () async {
      final root = await Directory.systemTemp.createTemp('atlas-c30-runtime-');
      addTearDown(() => root.delete(recursive: true));
      // The actual runtime/binding/epoch owner are used. Only platform custody
      // and document transport are test doubles; this is not native evidence.
      final native = RuntimeNativeHarness(root)..install();
      addTearDown(native.dispose);
      final fixture = RuntimeFixture();
      final owner = await fixture.initialize(
        native.binding.directory(RuntimeFixture.vaultID),
      );
      await native.binding.provision(
        owner: owner,
        signingSeed: runtimeTestKey(10),
      );
      final runtime = native.runtime();
      addTearDown(runtime.deactivate);
      expect(
        await runtime.activateExisting(RuntimeFixture.vaultID),
        AtlasVaultActivationResult.activated,
      );
      final before = await owner.observation();
      final identity = AtlasVaultPairingMemoryIdentityStore();
      addTearDown(identity.deletePrimaryIdentity);
      final transactions = AtlasVaultPairingMemoryTransactionStore();
      final staging = AtlasVaultPairingMemoryStageStore();
      final transport = AtlasVaultPairingMemoryTransport(
        AtlasVaultPairingMailbox(),
      );
      final coordinator = AtlasVaultTrustedPairingCoordinator(
        identityStore: identity,
        registryStore: AtlasVaultPairingMemoryRegistryStore(),
        replayStore: AtlasVaultPairingMemoryReplayStore(),
        transactionStore: transactions,
        stageStore: staging,
        artifactTransport: transport,
        runtime: runtime,
        cleanInstallProbe: () async =>
            AtlasVaultPairingCleanInstallDisposition.existingVault,
        authorizeKeyRelease: (_) async => false,
        identityGenerator: () => AtlasVaultDeviceIdentity.fromPrivateKeys(
          signingPrivateSeed: runtimeTestKey(10),
          agreementPrivateKey: runtimeTestKey(20),
          createdAt: '2026-09-05T00:00:00Z',
          expectedDeviceId: fixture.deviceID(0),
        ),
      );
      addTearDown(coordinator.stop);
      expect(
        (await coordinator.createDeviceIdentity()).disposition,
        AtlasVaultTrustedPairingDisposition.identityReady,
      );
      final result = await coordinator.createPairingOffer();
      expect(await owner.observation(), before);
      expect(result.sas == null, isTrue);
      expect(
        await staging.read(AtlasVaultPairingArtifactKind.delivery),
        isNull,
      );
      // Desired T74 integration behavior. Preserve RED until a reviewed
      // enrollment binding exists; never enable stale legacy export to pass it.
      expect(
        result.disposition,
        AtlasVaultTrustedPairingDisposition.offerReady,
      );
    },
  );
}
