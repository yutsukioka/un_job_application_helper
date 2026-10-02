import 'dart:async';
import 'dart:io';

import 'package:atlas/atlas_vault.dart';
import 'package:atlas/src/atlas_vault/private_state_runtime.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_runtime_native_harness.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  for (final wrongIdentity in [false, true]) {
    test(
      'C30 epoch-only ceremony checks active issuer: wrong=$wrongIdentity',
      () async {
        final root = await Directory.systemTemp.createTemp(
          'c30-epoch-ceremony-',
        );
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
        final identity = await AtlasVaultDeviceIdentity.fromPrivateKeys(
          signingPrivateSeed: runtimeTestKey(wrongIdentity ? 90 : 10),
          agreementPrivateKey: runtimeTestKey(wrongIdentity ? 91 : 20),
          createdAt: '2026-09-05T00:00:00Z',
        );
        addTearDown(identity.destroy);
        final before = await owner.observation();
        if (wrongIdentity) {
          var called = false;
          await expectLater(
            runtime.withEnrollmentContext(identity, (_) async {
              called = true;
            }),
            throwsException,
          );
          expect(called, isFalse);
        } else {
          final c = await runtime.withEnrollmentContext(identity, (
            context,
          ) async {
            await expectLater(
              runtime.withInteroperabilitySession((_) async {}),
              throwsException,
            );
            await expectLater(
              runtime.withEnrollmentContext(identity, (_) async {}),
              throwsException,
            );
            return context;
          });
          expect(c['key_epoch'], 4);
          expect(c['vault_id'], RuntimeFixture.vaultID);
          expect(c.keys.toSet(), {
            'account_id',
            'vault_id',
            'key_epoch',
            'registry_generation',
            'state_root',
            'activation_id',
          });
          final entered = Completer<void>(), release = Completer<void>();
          final ceremony = runtime.withEnrollmentContext(identity, (_) async {
            entered.complete();
            await release.future;
          });
          final rejected = expectLater(
            ceremony,
            throwsA(isA<AtlasVaultPrivateStateException>()),
          );
          await entered.future;
          final closing = runtime.deactivate();
          expect(runtime.isActive, isFalse);
          await expectLater(
            runtime.withEnrollmentContext(identity, (_) async {}),
            throwsA(isA<AtlasVaultPrivateStateException>()),
          );
          release.complete();
          await rejected;
          await closing;
        }
        expect(await owner.observation(), before);
        await runtime.deactivate();
        await expectLater(
          runtime.withEnrollmentContext(identity, (_) async {}),
          throwsException,
        );
      },
    );
  }
}
