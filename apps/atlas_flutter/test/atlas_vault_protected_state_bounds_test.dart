import 'dart:typed_data';

import 'package:atlas/atlas_vault.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/atlas_vault_pairing_fakes.dart';

void main() {
  test('protected-state categories enforce exact shared limits', () {
    const expected = <AtlasVaultProtectedStateCategory, int>{
      AtlasVaultProtectedStateCategory.trustedDeviceRegistry: 2 * 1024 * 1024,
      AtlasVaultProtectedStateCategory.pairingReplayState: 2 * 1024 * 1024,
      AtlasVaultProtectedStateCategory.pairingTransactionJournal: 64 * 1024,
      AtlasVaultProtectedStateCategory.pairingBootstrap: 128 * 1024 * 1024,
      AtlasVaultProtectedStateCategory.importedEncryptedState:
          128 * 1024 * 1024,
    };

    for (final entry in expected.entries) {
      expect(atlasVaultMaximumProtectedStateByteCount(entry.key), entry.value);
      expect(
        requireAtlasVaultProtectedStateByteCount(entry.key, entry.value),
        entry.value,
      );
      expect(
        () => requireAtlasVaultProtectedStateByteCount(
          entry.key,
          entry.value + 1,
        ),
        throwsA(isA<AtlasVaultProtectedStateBoundsException>()),
      );
    }
  });

  test('protected-state byte counts are positive integers', () {
    for (final invalid in <int>[0, -1]) {
      expect(
        () => requireAtlasVaultProtectedStateByteCount(
          AtlasVaultProtectedStateCategory.trustedDeviceRegistry,
          invalid,
        ),
        throwsA(isA<AtlasVaultProtectedStateBoundsException>()),
      );
    }
  });

  test('staged artifact aggregate is overflow safe and bounded', () {
    final half = atlasVaultMaximumStagedPairingArtifactByteCount ~/ 2;
    expect(
      requireAtlasVaultStagedPairingArtifactByteCounts(<int>[half, half]),
      atlasVaultMaximumStagedPairingArtifactByteCount,
    );
    expect(
      () => requireAtlasVaultStagedPairingArtifactByteCounts(<int>[
        half,
        half + 1,
      ]),
      throwsA(isA<AtlasVaultProtectedStateBoundsException>()),
    );
    expect(
      () => requireAtlasVaultStagedPairingArtifactByteCounts(<int>[
        1,
        1,
        1,
        1,
        1,
      ]),
      throwsA(isA<AtlasVaultProtectedStateBoundsException>()),
    );
  });

  test('oversized registry and replay fail at their read boundary', () {
    expect(
      () => AtlasVaultTrustedDeviceRegistry.fromCanonicalBytes(
        Uint8List(atlasVaultMaximumTrustedDeviceRegistryByteCount + 1),
      ),
      throwsA(isA<AtlasVaultTrustedDeviceStateException>()),
    );
    expect(
      () => AtlasVaultPairingReplayStore.fromCanonicalBytes(
        Uint8List(atlasVaultMaximumPairingReplayStateByteCount + 1),
      ),
      throwsA(isA<AtlasVaultTrustedDeviceStateException>()),
    );
  });

  test('oversized staged aggregate never reaches persistence', () async {
    final store = AtlasVaultPairingMemoryTransactionStore();
    final half = atlasVaultMaximumStagedPairingArtifactByteCount ~/ 2;

    expect(
      AtlasVaultPairingTransaction.fromJson(
        _transaction(<int>[half, half]),
      ).stagedArtifacts,
      hasLength(2),
    );
    await expectLater(() async {
      final transaction = AtlasVaultPairingTransaction.fromJson(
        _transaction(<int>[half, half + 1]),
      );
      await store.create(transaction);
    }, throwsA(isA<AtlasVaultPairingTransactionException>()));
    expect(store.events, isEmpty);
    expect(await store.read(), isNull);
  });
}

Map<String, Object?> _transaction(List<int> byteCounts) {
  return <String, Object?>{
    'format': 'atlasvault-pairing-transaction',
    'version': 1,
    'transaction_id': '42000000-0000-4000-8000-000000000001',
    'revision': '42000000-0000-4000-8000-000000000002',
    'parent_revision': null,
    'role': 'invitee',
    'stage': 'acceptance_created',
    'created_at': '2026-08-15T10:00:00Z',
    'updated_at': '2026-08-15T10:01:00Z',
    'installed_at': null,
    'local_device_id': 'avd1-${'a' * 64}',
    'peer_device_id': 'avd1-${'b' * 64}',
    'transcript_sha256': 'c' * 64,
    'offer_sha256': 'd' * 64,
    'acceptance_sha256': 'e' * 64,
    'delivery_sha256': null,
    'acknowledgement_sha256': null,
    'bootstrap_sha256': null,
    'vault_id': null,
    'key_epoch': null,
    'ephemeral_private_key': null,
    'store_sha256': null,
    'vault_key_sha256': null,
    'selection_committed': false,
    'staged_artifacts': <Object?>[
      <String, Object?>{
        'kind': 'offer',
        'sha256': 'd' * 64,
        'byte_count': byteCounts[0],
      },
      <String, Object?>{
        'kind': 'acceptance',
        'sha256': 'e' * 64,
        'byte_count': byteCounts[1],
      },
    ],
  };
}
