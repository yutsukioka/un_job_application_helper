import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('release Android manifest permits local job-api networking', () {
    final manifest = File(
      'android/app/src/main/AndroidManifest.xml',
    ).readAsStringSync();
    final networkConfig = File(
      'android/app/src/main/res/xml/network_security_config.xml',
    ).readAsStringSync();

    expect(
      manifest,
      contains('<uses-permission android:name="android.permission.INTERNET"/>'),
    );
    expect(
      manifest,
      contains('android:networkSecurityConfig="@xml/network_security_config"'),
    );
    expect(manifest, contains('android:usesCleartextTraffic="false"'));
    expect(manifest, isNot(contains('android:usesCleartextTraffic="true"')));
    expect(
      networkConfig,
      contains('<base-config cleartextTrafficPermitted="false">'),
    );
    expect(
      networkConfig,
      contains('<domain-config cleartextTrafficPermitted="true">'),
    );
    expect(
      networkConfig,
      contains('<domain includeSubdomains="false">10.253.1.43</domain>'),
    );
    expect(
      networkConfig,
      contains('<domain includeSubdomains="false">10.0.2.2</domain>'),
    );
    expect(
      networkConfig,
      contains('<domain includeSubdomains="false">127.0.0.1</domain>'),
    );
    expect(
      networkConfig,
      contains('<domain includeSubdomains="false">localhost</domain>'),
    );
  });
}
