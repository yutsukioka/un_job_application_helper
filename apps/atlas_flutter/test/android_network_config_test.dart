import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'release Android networking keeps LAN cleartext out of shipped config',
    () {
      final manifest = File(
        'android/app/src/main/AndroidManifest.xml',
      ).readAsStringSync();
      final releaseNetworkConfig = File(
        'android/app/src/main/res/xml/network_security_config.xml',
      ).readAsStringSync();
      final debugNetworkConfig = File(
        'android/app/src/debug/res/xml/network_security_config.xml',
      ).readAsStringSync();
      final profileNetworkConfig = File(
        'android/app/src/profile/res/xml/network_security_config.xml',
      ).readAsStringSync();

      expect(
        manifest,
        contains(
          '<uses-permission android:name="android.permission.INTERNET"/>',
        ),
      );
      expect(
        manifest,
        contains(
          'android:networkSecurityConfig="@xml/network_security_config"',
        ),
      );
      expect(manifest, contains('android:usesCleartextTraffic="false"'));
      expect(manifest, isNot(contains('android:usesCleartextTraffic="true"')));
      expect(
        releaseNetworkConfig,
        contains('<base-config cleartextTrafficPermitted="false">'),
      );
      expect(
        releaseNetworkConfig,
        contains('<domain-config cleartextTrafficPermitted="true">'),
      );
      expect(
        releaseNetworkConfig,
        isNot(
          contains('<domain includeSubdomains="false">10.253.1.43</domain>'),
        ),
      );
      expect(
        releaseNetworkConfig,
        contains('<domain includeSubdomains="false">10.0.2.2</domain>'),
      );
      expect(
        releaseNetworkConfig,
        contains('<domain includeSubdomains="false">127.0.0.1</domain>'),
      );
      expect(
        releaseNetworkConfig,
        contains('<domain includeSubdomains="false">localhost</domain>'),
      );
      for (final localConfig in [debugNetworkConfig, profileNetworkConfig]) {
        expect(
          localConfig,
          contains('<domain includeSubdomains="false">10.253.1.43</domain>'),
        );
      }
    },
  );
}
