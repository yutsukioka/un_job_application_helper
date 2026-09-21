import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Atlas external URL policy', () {
    test('allows only browser and mail schemes', () {
      for (final rawValue in <String>[
        'https://jobs.example.org/apply',
        'http://jobs.example.org/source',
        'mailto:jobs@example.org',
        'HTTPS://jobs.example.org/apply',
      ]) {
        final uri = Uri.parse(rawValue);
        expect(isAllowedAtlasExternalURL(uri), isTrue, reason: rawValue);
        expect(safeAtlasExternalURL(uri), uri, reason: rawValue);
      }

      for (final rawValue in <String>[
        'javascript:alert(1)',
        'file:///private/tmp/secrets.txt',
        'data:text/html;base64,PGgxPkJhZDwvaDE+',
        'ftp://jobs.example.org/file',
      ]) {
        final uri = Uri.parse(rawValue);
        expect(isAllowedAtlasExternalURL(uri), isFalse, reason: rawValue);
        expect(safeAtlasExternalURL(uri), isNull, reason: rawValue);
      }
    });
  });
}
