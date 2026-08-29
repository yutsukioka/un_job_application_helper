import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Windows recovery process stages avoid widget teardown', () {
    final source = File(
      'integration_test/'
      'atlas_vault_windows_interoperability_recovery_test.dart',
    ).readAsStringSync();
    final processRegistration = _section(
      source,
      'void _registerCrossProcessRecoveryTest(String stage) {',
      'Future<void> _runCrossProcessRecoveryStage(String stage) async {',
    );

    expect(
      source,
      contains(
        'if (_processStage == null) {\n'
        '    _registerNormalRecoveryTests();\n'
        '  } else {\n'
        '    _registerCrossProcessRecoveryTest(_processStage!);',
      ),
    );
    expect(processRegistration, contains("  test('Windows import admission"));
    expect(processRegistration, isNot(contains('testWidgets(')));
    expect(processRegistration, isNot(contains('WidgetTester')));
    expect(source, isNot(contains('FlutterError.onError')));
    expect(source, isNot(contains('runZonedGuarded')));
    expect(
      source,
      isNot(contains('FocusManager was used after being disposed')),
    );
  });

  test(
    'AtlasAppController lifecycle code does not suppress disposal errors',
    () {
      final source = File(
        'lib/features/app_shell/atlas_app.dart',
      ).readAsStringSync();

      expect(source, isNot(contains('FlutterError.onError')));
      expect(source, isNot(contains('runZonedGuarded')));
      expect(
        source,
        isNot(contains('AtlasAppController was used after being disposed')),
      );
    },
  );
}

String _section(String source, String startMarker, String endMarker) {
  final start = source.indexOf(startMarker);
  final end = source.indexOf(endMarker, start + startMarker.length);
  expect(start, greaterThanOrEqualTo(0));
  expect(end, greaterThan(start));
  return source.substring(start, end);
}
