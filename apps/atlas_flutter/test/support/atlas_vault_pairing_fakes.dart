import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

typedef AtlasVaultPairingMethodHandler =
    Future<Object?> Function(MethodCall call);

final class AtlasVaultPairingMethodCallRecorder {
  AtlasVaultPairingMethodCallRecorder({required this.channelName})
    : channel = MethodChannel(channelName);

  final String channelName;
  final MethodChannel channel;
  final List<MethodCall> calls = <MethodCall>[];
  AtlasVaultPairingMethodHandler? handler;

  void install() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
          calls.add(call);
          return handler?.call(call);
        });
  }

  void uninstall() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  }
}
