import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:cryptography/cryptography.dart';

Map<String,Object?> m(Object? x)=>Map<String,Object?>.from(x as Map);
List<Map<String,Object?>> rows(Object? x)=>(x as List).map(m).toList();
Future<void> main(List<String> args) async {
  final root=Directory(args[0]),stage=args[1];
  final v=m(jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_epoch_catch_up_history_v2.json').readAsStringSync()));
  final view=m(v['initial_view']),packets=rows((v['packets'] as List)[2]);
  final c=AtlasVaultEpochVault(root,storageKey:Uint8List.fromList(List.filled(32,52)),deviceID:(v['device_ids'] as List)[2] as String,registry:rows(v['initial_registry']),accountID:view['account_id'] as String,vaultID:'vault-c26',keyEpoch:3,stateRoot:view['root'] as String);
  final signing=await Ed25519().newKeyPairFromSeed(List.filled(32,10));
  if(!await File('${root.path}/activation').exists()) {
    await root.create(recursive:true);
    final h=AtlasVaultGuardedSyncState(file:File('${root.path}/initial-history'),encryptionKey:Uint8List.fromList(List.filled(32,62)),accountId:view['account_id'] as String,vaultId:'vault-c26',collectionId:'collection-c26',keyEpoch:3,trustedSigner:Uint8List.fromList((await signing.extractPublicKey()).bytes));
    await h.initialize();
    await h.ingest(view,rows(v['initial_history_registry']),m(v['initial_collection']),base64Decode(v['opaque_state_b64'] as String));
    await c.initialize({3:Uint8List.fromList(List.filled(32,30))},history:h);
  }
  Future<void> point(String name) async {
    if(name==stage){await File('${root.path}/ready').writeAsString(name,flush:true);while(true){await Future<void>.delayed(const Duration(seconds:60));}}
  }
  if(stage=='observe') {
    try {await c.observation();}catch(_){await c.recoverPublication();}
    final o=await c.observation();var fenced=false;
    try {await c.seal('patch',Uint8List.fromList([7]),objectID:'probe',revision:'r1',signingKey:await Ed25519().newKeyPairFromSeed(List.filled(32,12)));}catch(_){fenced=true;}
    stdout.writeln(jsonEncode({...o,'writes_fenced':fenced}));return;
  }
  if((await c.observation())['status']!='CLEANUP_PENDING') {
    await c.catchUpForTesting(packets,currentActivationID:v['target_activation_id'] as String,agreementPrivateKey:Uint8List.fromList(List.filled(32,22)),historyUpdates:rows(v['history_updates']),checkpoint:point);
  }
  if(stage=='cleanup_pending'||stage=='deleted_epoch'||stage=='cleanup_resume'||(await c.observation())['status']=='CLEANUP_PENDING') {
    await c.cleanupEpochsForTesting(retainEpochs:{5},deleteEpoch:(e) async {await File('${root.path}/deleted-$e').writeAsString('deleted',flush:true);},containsEpoch:(e) async=>!await File('${root.path}/deleted-$e').exists(),checkpoint:point);
  }
  stdout.writeln(jsonEncode(await c.observation()));
}
