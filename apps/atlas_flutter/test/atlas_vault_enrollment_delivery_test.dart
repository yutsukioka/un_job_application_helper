import 'dart:convert';
import 'dart:io';

import 'package:atlas/src/atlas_vault/device_identity.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_runtime_fixtures.dart';
import 'support/atlas_vault_vector_loader.dart';

void main() {
  final cases = runtimeRows(
    loadAtlasVaultVector('atlasvault_enrollment_delivery_v1.json')['cases'],
  );
  Future<AtlasVaultDeviceIdentity> recipient([int offset = 0]) =>
      AtlasVaultDeviceIdentity.fromPrivateKeys(
        signingPrivateSeed: runtimeTestKey(90 + offset),
        agreementPrivateKey: runtimeTestKey(100 + offset),
        createdAt: '2026-01-01T00:00:00Z',
        keyEpoch: 3,
      );
  for (var i = 0; i < cases.length; i++) {
    test(
      'C30 secure enrollment delivery installs historical authority $i',
      () async {
        final root = await Directory.systemTemp.createTemp('c30-delivery-');
        addTearDown(() => root.delete(recursive: true));
        final device = await recipient();
        addTearDown(device.destroy);
        final v = cases[i];
        Future<AtlasVaultEpochVault> install() =>
            AtlasVaultEnrollmentDelivery.install(
              Directory('${root.path}/recipient'),
              runtimeObject(v['packet']),
              pins: runtimeObject(v['pins']),
              trustedSigner: base64Decode(v['trusted_signer_b64']! as String),
              recipient: device,
              agreementPrivateKey: runtimeTestKey(100),
              storageKey: runtimeTestKey(111),
            );
        final owner = await install();
        final bytes = await owner.open(
          AtlasVaultOpaqueCiphertextEnvelope.fromJson(
            runtimeObject(v['envelope']),
          ),
        );
        try {
          final digest = (await Sha256().hash(
            bytes,
          )).bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
          expect(digest == v['opened_sha256'], isTrue);
        } finally {
          bytes.fillRange(0, bytes.length, 0);
        }
        expect(
          await (await install()).observation(),
          await owner.observation(),
        );
      },
    );
  }
  for (final attack in [
    'wrapper',
    'missing',
    'duplicate',
    'reorder',
    'recipient',
    'anchor',
    'authority',
    'suite',
    'signature',
  ]) {
    test('C30 secure enrollment rejects $attack before publication', () async {
      final root = await Directory.systemTemp.createTemp('c30-delivery-');
      addTearDown(() => root.delete(recursive: true));
      final device = await recipient(attack == 'recipient' ? 1 : 0);
      addTearDown(device.destroy);
      final v = cases[0],
          p = runtimeObject(jsonDecode(jsonEncode(cases[0]['packet'])));
      final rows = p['deliveries']! as List;
      switch (attack) {
        case 'wrapper':
          (rows[0] as Map)['ciphertext_b64'] = base64Encode(List.filled(48, 0));
        case 'missing':
          rows.removeLast();
        case 'duplicate':
          rows.add(rows[0]);
        case 'reorder':
          p['deliveries'] = rows.reversed.toList();
        case 'anchor':
          ((p['anchor'] as Map)['checkpoint'] as Map)['root'] = '01' * 32;
        case 'authority':
          (p['historical_authority'] as Map)['views'] =
              ((p['historical_authority'] as Map)['views'] as List).reversed
                  .toList();
        case 'suite':
          p['hpke_suite'] = 'wrong';
        case 'signature':
          p['signature_b64'] = base64Encode(List.filled(64, 0));
      }
      final destination = Directory('${root.path}/recipient');
      await expectLater(
        AtlasVaultEnrollmentDelivery.install(
          destination,
          p,
          pins: runtimeObject(v['pins']),
          trustedSigner: base64Decode(v['trusted_signer_b64']! as String),
          recipient: device,
          agreementPrivateKey: runtimeTestKey(
            attack == 'recipient' ? 101 : 100,
          ),
          storageKey: runtimeTestKey(111),
        ),
        throwsA(isA<AtlasVaultEnrollmentDeliveryException>()),
      );
      expect(await destination.exists(), isFalse);
    });
  }
}
