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
    'C30 epoch pairing reaches recipient-bound delivery after SAS',
    () async {
      final root = await Directory.systemTemp.createTemp('atlas-c30-ceremony-');
      addTearDown(() => root.delete(recursive: true));
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
      // Independent coordinator stores; only document exchange is shared.
      // Native custody is injected. This is not a real-device or restart proof.
      final mailbox = AtlasVaultPairingMailbox();
      final identities = List.generate(
        2,
        (_) => AtlasVaultPairingMemoryIdentityStore(),
      );
      final stages = List.generate(
        2,
        (_) => AtlasVaultPairingMemoryStageStore(),
      );
      final recipientRuntime = AtlasVaultPrivateStateRuntime(
        secureKeyStore: AtlasVaultPairingMemorySecureKeyStore(),
        localStoreIO: AtlasVaultPairingMemoryLocalStore(),
      );
      addTearDown(recipientRuntime.deactivate);
      final devices = List.generate(
        2,
        (index) => AtlasVaultTrustedPairingCoordinator(
          identityStore: identities[index],
          registryStore: AtlasVaultPairingMemoryRegistryStore(),
          replayStore: AtlasVaultPairingMemoryReplayStore(),
          transactionStore: AtlasVaultPairingMemoryTransactionStore(),
          stageStore: stages[index],
          artifactTransport: AtlasVaultPairingMemoryTransport(mailbox),
          runtime: index == 0 ? runtime : recipientRuntime,
          cleanInstallProbe: () async => index == 0
              ? AtlasVaultPairingCleanInstallDisposition.existingVault
              : AtlasVaultPairingCleanInstallDisposition.clean,
          authorizeKeyRelease: (_) async => true,
          identityGenerator: () => AtlasVaultDeviceIdentity.fromPrivateKeys(
            signingPrivateSeed: runtimeTestKey(index == 0 ? 10 : 90),
            agreementPrivateKey: runtimeTestKey(index == 0 ? 20 : 91),
            createdAt: '2026-09-05T00:00:00Z',
          ),
        ),
      );
      addTearDown(() async {
        for (final device in devices) {
          await device.stop();
        }
        for (final identity in identities) {
          await identity.deletePrimaryIdentity();
        }
      });
      for (final device in devices) {
        expect(
          (await device.createDeviceIdentity()).disposition,
          AtlasVaultTrustedPairingDisposition.identityReady,
        );
      }
      final inviter = devices[0], invitee = devices[1];
      expect(
        (await inviter.createPairingOffer()).disposition,
        AtlasVaultTrustedPairingDisposition.offerReady,
      );
      expect(
        (await inviter.savePairingOffer()).disposition,
        AtlasVaultTrustedPairingDisposition.offerSaved,
      );
      expect(
        (await invitee.importPairingOffer()).disposition,
        AtlasVaultTrustedPairingDisposition.acceptanceReady,
      );
      expect(
        (await invitee.savePairingAcceptance()).disposition,
        AtlasVaultTrustedPairingDisposition.acceptanceSaved,
      );
      final senderCodes = await inviter.importPairingAcceptance();
      final recipientCodes = await invitee.inspect();
      expect(
        senderCodes.disposition,
        AtlasVaultTrustedPairingDisposition.codesReady,
      );
      expect(
        senderCodes.sas != null && senderCodes.sas == recipientCodes.sas,
        isTrue,
      );
      expect(
        (await inviter.confirmCodesMatch(
          expectedTranscriptSha256: senderCodes.transcriptSha256,
        )).disposition,
        AtlasVaultTrustedPairingDisposition.deliveryReady,
      );
      expect(
        await stages[0].read(AtlasVaultPairingArtifactKind.delivery),
        isNotNull,
      );
    },
  );
}
