import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const runSmokeValue = String.fromEnvironment('RUN_WINDOWS_SMOKE');
  const runSmoke = runSmokeValue == '1' || runSmokeValue == 'true';
  final skipReason = !runSmoke
      ? 'Set RUN_WINDOWS_SMOKE=1 to run the Windows job-api smoke test.'
      : !Platform.isWindows
      ? 'Windows-only job-api smoke test.'
      : false;

  test('Windows can reach the configured job-api health endpoint', () async {
    const rawBaseURL = String.fromEnvironment('ATLAS_API_BASE_URL');
    final baseURL = AtlasAPIClient.normalizedBaseURL(rawBaseURL);
    expect(
      baseURL,
      isNotNull,
      reason: 'ATLAS_API_BASE_URL must be a valid http(s) base URL.',
    );

    final healthURL = baseURL!.resolve('api/health');
    final client = HttpClient();
    try {
      final request = await client
          .getUrl(healthURL)
          .timeout(const Duration(seconds: 5));
      final response = await request.close().timeout(
        const Duration(seconds: 5),
      );
      final body = await utf8.decoder
          .bind(response)
          .join()
          .timeout(const Duration(seconds: 5));

      expect(
        response.statusCode,
        HttpStatus.ok,
        reason: 'GET $healthURL returned ${response.statusCode}: $body',
      );
      expect(body.trim(), isNotEmpty);
    } on TimeoutException catch (error) {
      fail('GET $healthURL timed out: $error');
    } finally {
      client.close(force: true);
    }
  }, skip: skipReason);
}
