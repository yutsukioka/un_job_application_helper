import 'epoch_rotation.dart';
import 'plaintext_migration.dart';
import 'sync_queue.dart';

/// Application-accessible deletion only; not physical or remote erasure.
extension AtlasVaultEpochSecureCleanup on AtlasVaultEpochVault {
  Future<void> cleanupSecureStorage({
    required Set<int> retainEpochs,
    required Map<int,String> epochStorageIDs,
    required AtlasVaultMigrationSecureKeyStore store,
  }) async {
    final ids=Map<int,String>.from(epochStorageIDs);
    final available=await availableEpochs();
    if(ids.length>32 || ids.values.toSet().length!=ids.length ||
       available.any((e)=>!ids.containsKey(e))) {
      throw const AtlasVaultRotationException('ATLAS_CLEANUP_PENDING');
    }
    await cleanupEpochs(retainEpochs:Set<int>.from(retainEpochs),
      deleteEpoch:(e)=>store.deleteVaultKey(ids[e]!),
      containsEpoch:(e) async {
        final value=await store.loadVaultKey(ids[e]!);
        final present=value!=null;
        value?.fillRange(0,value.length,0);
        return present;
      });
  }
}
