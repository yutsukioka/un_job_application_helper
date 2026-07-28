import '../../atlas_vault.dart';

abstract interface class AtlasVaultLocalStoreIO {
  Future<AtlasVaultLocalStore?> read(String vaultId);

  Future<void> create(String vaultId, AtlasVaultLocalStore store);

  Future<void> replace(
    String vaultId,
    AtlasVaultLocalStore store, {
    required String expectedSha256,
  });

  Future<void> delete(String vaultId);
}
