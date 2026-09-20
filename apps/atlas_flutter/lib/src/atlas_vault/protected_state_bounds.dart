const atlasVaultMaximumTrustedDeviceRegistryByteCount = 2 * 1024 * 1024;
const atlasVaultMaximumPairingReplayStateByteCount = 2 * 1024 * 1024;
const atlasVaultMaximumPairingTransactionJournalByteCount = 64 * 1024;
const atlasVaultMaximumPairingBootstrapByteCount = 128 * 1024 * 1024;
const atlasVaultMaximumImportedEncryptedStateByteCount = 128 * 1024 * 1024;
const atlasVaultMaximumStagedPairingArtifactByteCount = 128 * 1024 * 1024;
const atlasVaultMaximumStagedPairingArtifactCount = 4;

enum AtlasVaultProtectedStateCategory {
  trustedDeviceRegistry,
  pairingReplayState,
  pairingTransactionJournal,
  pairingBootstrap,
  importedEncryptedState,
}

final class AtlasVaultProtectedStateBoundsException implements Exception {
  const AtlasVaultProtectedStateBoundsException();

  @override
  String toString() => 'AtlasVault protected-state size is invalid.';
}

int atlasVaultMaximumProtectedStateByteCount(
  AtlasVaultProtectedStateCategory category,
) {
  return switch (category) {
    AtlasVaultProtectedStateCategory.trustedDeviceRegistry =>
      atlasVaultMaximumTrustedDeviceRegistryByteCount,
    AtlasVaultProtectedStateCategory.pairingReplayState =>
      atlasVaultMaximumPairingReplayStateByteCount,
    AtlasVaultProtectedStateCategory.pairingTransactionJournal =>
      atlasVaultMaximumPairingTransactionJournalByteCount,
    AtlasVaultProtectedStateCategory.pairingBootstrap =>
      atlasVaultMaximumPairingBootstrapByteCount,
    AtlasVaultProtectedStateCategory.importedEncryptedState =>
      atlasVaultMaximumImportedEncryptedStateByteCount,
  };
}

int requireAtlasVaultProtectedStateByteCount(
  AtlasVaultProtectedStateCategory category,
  int byteCount,
) {
  if (byteCount <= 0 ||
      byteCount > atlasVaultMaximumProtectedStateByteCount(category)) {
    throw const AtlasVaultProtectedStateBoundsException();
  }
  return byteCount;
}

int requireAtlasVaultStagedPairingArtifactByteCounts(Iterable<int> byteCounts) {
  var total = 0;
  var count = 0;
  for (final byteCount in byteCounts) {
    count += 1;
    if (count > atlasVaultMaximumStagedPairingArtifactCount ||
        byteCount <= 0 ||
        byteCount > atlasVaultMaximumStagedPairingArtifactByteCount ||
        total > atlasVaultMaximumStagedPairingArtifactByteCount - byteCount) {
      throw const AtlasVaultProtectedStateBoundsException();
    }
    total += byteCount;
  }
  return total;
}
