import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter_test/flutter_test.dart';
import 'support/atlas_vault_dart_helper_process.dart';

final v = jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json').readAsStringSync()) as Map;
final packets = Map<String,dynamic>.from(v['packets'] as Map);
AtlasVaultGuardedSyncState client(File file) => AtlasVaultGuardedSyncState(file:file,encryptionKey:Uint8List.fromList(List.generate(32,(i)=>i)),accountId:'account_c22',vaultId:'vault_c22',collectionId:'collection_c21',keyEpoch:2,trustedSigner:base64Decode(v['signing_public_b64'] as String));
Future<bool> serve(String name, AtlasVaultGuardedSyncState c) {
  final p=packets[name] as Map;
  return c.ingest(Map<String,Object?>.from(p['view'] as Map),(p['registry'] as List).map((e)=>Map<String,Object?>.from(e as Map)).toList(),Map<String,Object?>.from(p['collection'] as Map),base64Decode(p['opaque_b64'] as String));
}
Future<AtlasVaultGuardedSyncState> ready(File path) async {
  final c=client(path);await c.initialize();await serve('one',c);await serve('two',c);return c;
}
Matcher code(String value)=>throwsA(isA<AtlasVaultStateViewException>().having((e)=>e.code,'code',value));
void main() {
  late Directory dir;
  setUp(() async {dir=await Directory.systemTemp.createTemp('c23-');});
  tearDown(() async {await dir.delete(recursive:true);});
  for(final attack in v['attacks'] as List) {
    test('malicious server ${attack['name']} preserves terminal state and fences',() async {
      final a=await ready(File('${dir.path}/A')),b=await ready(File('${dir.path}/B'));
      final before=await b.checkpoint();
      await expectLater(serve(attack['packet'] as String,b),code(attack['reason'] as String));
      final reopened=client(File('${dir.path}/B'));
      expect(await reopened.checkpoint(),before);expect(await a.checkpoint(),before);
      final ui=await reopened.recovery();expect(ui['status'],'MANUAL_REQUIRED');expect(ui['reason'],attack['reason']);
      var calls=0;
      await expectLater(reopened.automaticSync(() async {calls++;}),code('ATLAS_RECOVERY_PENDING'));
      await expectLater(serve('three',reopened),code('ATLAS_RECOVERY_PENDING'));expect(calls,0);
      final text=jsonEncode(ui);expect(text.length,lessThan(160000));
      for(final s in ['ciphertext_b64','opaque_b64','passphrase','vault_key','access_token','nonce_b64']) {expect(text, isNot(contains(s)));}
    });
  }
  test('independent forks remain pending after explicit selection',() async {
    final a=await ready(File('${dir.path}/A')),b=client(File('${dir.path}/B'));await b.initialize();
    await serve('one',b);await serve('fork_two',b);
    final left=await a.exportEvidence(),right=await b.exportEvidence(),before=await a.checkpoint();
    await expectLater(a.compareEvidence(right),code('ATLAS_STATE_EQUIVOCATION'));
    final evidence=await a.evidence();expect(evidence['local'],left);expect(evidence['peer'],right);
    final ui=await a.recovery();
    expect(await a.resolve('select_peer',(ui['local'] as List).last['root'] as String,(ui['peer'] as List).last['root'] as String),'RECOVERY_PENDING');
    final reopened=client(File('${dir.path}/A'));expect(await reopened.evidence(),evidence);expect(await reopened.checkpoint(),before);
    expect((await reopened.recovery())['disposition'],'select_peer');
  });
  test('manual known-replay rejection resumes without advancing or losing evidence',() async {
    final c=await ready(File('${dir.path}/C')),before=await c.checkpoint();
    await expectLater(serve('one',c),code('ATLAS_ROLLBACK_REJECTED'));
    final ui=await c.recovery(),local=(ui['local'] as List).last['root'] as String,peer=(ui['peer'] as List).last['root'] as String;
    await expectLater(c.resolve('retain_accepted','f'*64,peer),throwsA(isA<AtlasVaultStateViewException>()));
    expect(await c.resolve('retain_accepted',local,peer),'ACTIVE');expect(await c.checkpoint(),before);
    expect((await c.evidence())['peer'],[packets['one']['view']]);
    expect(await c.automaticSync(() async=>7),7);expect(await serve('three',c),true);
    final accepted=await c.checkpoint();expect(await serve('three',c),false);expect(await c.checkpoint(),accepted);
  });
  test('alarm survives SIGKILL and fresh process reopen',() async {
    final path=File('${dir.path}/child'),signal=File('${dir.path}/ready');
    final process=await startAtlasVaultDartHelper('test/support/atlas_vault_sync_recovery_process.dart',[path.path,signal.path]);
    final errors=process.stderr.transform(utf8.decoder).join();process.stdout.drain<void>();
    try {
      final limit=DateTime.now().add(const Duration(seconds:30));
      while(!await signal.exists()&&DateTime.now().isBefore(limit)) {await Future<void>.delayed(const Duration(milliseconds:50));}
      if(!await signal.exists()) {process.kill(ProcessSignal.sigkill);fail('child did not persist alarm: ${await errors}');}
      expect(process.kill(ProcessSignal.sigkill),true);await process.exitCode;
      final c=client(path);expect((await c.recovery())['status'],'MANUAL_REQUIRED');
      await expectLater(c.automaticSync(() async=>0),code('ATLAS_RECOVERY_PENDING'));
    } finally {process.kill(ProcessSignal.sigkill);await process.exitCode;}
  });
}
