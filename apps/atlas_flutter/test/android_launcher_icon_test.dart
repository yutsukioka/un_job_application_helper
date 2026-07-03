import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android launcher icon uses Atlas artwork at every density', () {
    final sourceIcon = File('../apple/Design/AppIcon/AppIcon-iOS-1024.png');
    expect(sourceIcon.existsSync(), isTrue);

    const expectations = <String, ({int size, int minBytes})>{
      'mdpi': (size: 48, minBytes: 3000),
      'hdpi': (size: 72, minBytes: 6000),
      'xhdpi': (size: 96, minBytes: 10000),
      'xxhdpi': (size: 144, minBytes: 20000),
      'xxxhdpi': (size: 192, minBytes: 30000),
    };

    for (final entry in expectations.entries) {
      final density = entry.key;
      final (:size, :minBytes) = entry.value;
      final icon = File(
        'android/app/src/main/res/mipmap-$density/ic_launcher.png',
      );

      expect(icon.existsSync(), isTrue, reason: '$density launcher icon');
      final bytes = icon.readAsBytesSync();
      expect(bytes.length, greaterThan(minBytes), reason: density);
      expect(_pngDimensions(bytes), (width: size, height: size));
    }
  });
}

({int width, int height}) _pngDimensions(Uint8List bytes) {
  const pngSignature = <int>[137, 80, 78, 71, 13, 10, 26, 10];
  expect(bytes.take(pngSignature.length), pngSignature);
  return (
    width: bytes.buffer.asByteData().getUint32(16),
    height: bytes.buffer.asByteData().getUint32(20),
  );
}
