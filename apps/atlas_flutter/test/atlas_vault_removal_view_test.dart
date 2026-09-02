import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:atlas/src/atlas_vault/removal_view.dart';
import 'package:atlas/src/atlas_vault/sync_queue.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  for (final platform in [TargetPlatform.android, TargetPlatform.windows]) {
    test('fresh platform removal prompt is strict on $platform', () async {
      final channel = MethodChannel('c25/$platform');
      var calls = 0;
      Object? result;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
        expect(call.method, 'authorizeDeviceRemoval'); expect(call.arguments, isNull); calls++;
        if (result is Exception) throw result!;
        return result;
      });
      final auth = AtlasVaultDeviceRemovalAuthorizer(platform: platform, channel: channel);
      for (result in [false, null, 'true', 1, {'ok': true}, PlatformException(code: 'private-sentinel')]) {
        expect(await auth.authorize(), isFalse);
      }
      result = true;
      expect(await auth.authorize(), isTrue); expect(await auth.authorize(), isTrue);
      expect(calls, 8);
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null);
    });
  }
  testWidgets('exact target confirmation, safe UI and disposal cancellation', (tester) async {
    final dir = await Directory.systemTemp.createTemp('c25-ui-');
    try {
      final v = jsonDecode(File('../../contracts/sync/test_vectors/atlasvault_revocation_v1.json').readAsStringSync()) as Map;
      final entries = (v['registry'] as List).map((e) => Map<String, Object?>.from(e as Map)).toList();
      final registry = AtlasVaultRevocationRegistry(file: File('${dir.path}/registry'), encryptionKey: Uint8List.fromList(List.filled(32, 7)), accountId: 'account-c25', vaultId: 'vault-c25', keyEpoch: 3, registry: entries, stateRoot: List.filled(32, 'ab').join());
      await registry.initialize();
      final prompt = Completer<Object?>(); var prompts = 0;
      final controller = AtlasVaultRemovalController(registry: registry, initiator: entries[0]['device_id'] as String, authorize: () { prompts++; return prompt.future; }, sign: (_) async { fail('disposed removal must not sign'); });
      await tester.pumpWidget(MaterialApp(home: AtlasVaultDeviceRemovalView(controller: controller, targetDevice: entries[1]['device_id'] as String, keyEpoch: 3)));
      await tester.pumpAndSettle();
      expect(prompts, 0);
      final allText = tester.widgetList<Text>(find.byType(Text)).map((t) => t.data ?? '').join('\n');
      expect(allText, contains(entries[1]['device_id']));
      expect(allText, isNot(contains(entries[1]['signing_public_b64'])));
      expect(allText, contains('3')); expect(allText, contains('4'));
      expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed, isNull);
      await tester.tap(find.byType(Checkbox)); await tester.pump();
      await tester.tap(find.byType(FilledButton)); await tester.pump();
      expect(prompts, 1);
      await tester.pumpWidget(const SizedBox());
      prompt.complete(true); await tester.pumpAndSettle();
      expect((await registry.snapshot())['sequence'], 0);
      expect(tester.takeException(), isNull);
    } finally { await dir.delete(recursive: true); }
  });
}
