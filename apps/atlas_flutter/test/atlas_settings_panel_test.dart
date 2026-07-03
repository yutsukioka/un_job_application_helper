import 'package:atlas/atlas.dart';
import 'package:atlas/features/app_shell/atlas_app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
    'settings tab exposes server status local save and local API setup controls',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AtlasSettingsPanel(
              clientFactory: (_) => AtlasAPIClient(
                baseURL: Uri.parse('http://127.0.0.1:8765'),
                transport: _HealthTransport(),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Server'), findsOneWidget);
      expect(find.widgetWithText(TextField, 'API base URL'), findsOneWidget);
      expect(find.text('Saved server'), findsOneWidget);
      expect(find.text('http://127.0.0.1:8765'), findsWidgets);
      expect(find.text('Test'), findsOneWidget);
      expect(find.text('Save and Reload'), findsOneWidget);
      expect(find.text('Status'), findsOneWidget);
      expect(find.text('Not connected'), findsOneWidget);
      expect(find.text('Local Save'), findsOneWidget);
      expect(find.text('Last updated'), findsOneWidget);
      expect(find.text('Cache status'), findsOneWidget);
      expect(find.text('Clear Local Cache'), findsOneWidget);
      expect(find.text('Cached jobs'), findsOneWidget);
      expect(find.text('Cached details'), findsOneWidget);
      expect(find.text('Auto refresh'), findsOneWidget);
      expect(find.text('Refresh Local Save Now'), findsOneWidget);
      expect(find.text('Local API Setup'), findsOneWidget);
      expect(find.textContaining('Use your Mac LAN URL'), findsOneWidget);
      expect(find.textContaining('Use http://10.0.2.2:8765'), findsOneWidget);
    },
  );

  testWidgets(
    'settings test connection normalizes URL and reports health summary',
    (tester) async {
      final requestedBaseURLs = <Uri>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AtlasSettingsPanel(
              clientFactory: (baseURL) {
                requestedBaseURLs.add(baseURL);
                return AtlasAPIClient(
                  baseURL: baseURL,
                  transport: _HealthTransport(),
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.widgetWithText(TextField, 'API base URL'),
        '  192.168.1.20:8765/api/health?probe=1 ',
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Test'));
      await tester.pumpAndSettle();

      expect(requestedBaseURLs.single.toString(), 'http://192.168.1.20:8765');
      expect(
        find.text('Connected: ok, 128 open jobs, 12 enabled sources.'),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'settings save and reload persists normalized server after health succeeds',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AtlasSettingsPanel(
              clientFactory: (baseURL) => AtlasAPIClient(
                baseURL: baseURL,
                transport: _HealthAndSearchTransport(),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.widgetWithText(TextField, 'API base URL'),
        'https://atlas.example.org/api/health',
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save and Reload'));
      await tester.pumpAndSettle();

      expect(find.text('Saved server'), findsOneWidget);
      expect(find.textContaining('https://atlas.example.org'), findsWidgets);
      expect(
        find.text('Saved https://atlas.example.org and refreshed 1 job.'),
        findsOneWidget,
      );
      expect(find.text('Programme Analyst'), findsOneWidget);
    },
  );

  testWidgets('settings reports invalid URLs and connection failures', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AtlasSettingsPanel(
            clientFactory: (baseURL) => AtlasAPIClient(
              baseURL: baseURL,
              transport: _FailingTransport(),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.widgetWithText(TextField, 'API base URL'),
      'ftp://server',
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Test'));
    await tester.pumpAndSettle();

    expect(
      find.text('Enter a valid http:// or https:// API base URL.'),
      findsOneWidget,
    );

    await tester.tap(find.text('Save and Reload'));
    await tester.pumpAndSettle();
    expect(
      find.text('Enter a valid http:// or https:// API base URL.'),
      findsOneWidget,
    );

    await tester.enterText(
      find.widgetWithText(TextField, 'API base URL'),
      'http://10.253.1.43:8765',
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Test'));
    await tester.pumpAndSettle();

    expect(find.textContaining('Connection failed:'), findsOneWidget);

    await tester.tap(find.text('Save and Reload'));
    await tester.pumpAndSettle();

    expect(find.textContaining('Save failed:'), findsOneWidget);
  });

  testWidgets('settings updates refresh interval and local save message', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AtlasSettingsPanel(
            clientFactory: (_) => AtlasAPIClient(
              baseURL: Uri.parse('http://127.0.0.1:8765'),
              transport: _HealthAndSearchTransport(),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Every 24 hours'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Every 24 hours'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Weekly').last);
    await tester.pumpAndSettle();
    expect(find.text('Weekly'), findsOneWidget);

    await tester.ensureVisible(find.text('Refresh Local Save Now'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh Local Save Now'));
    await tester.pumpAndSettle();
    expect(
      find.text('Local save refreshed: 1 job cached on this device.'),
      findsOneWidget,
    );
    expect(find.text('1'), findsWidgets);
    expect(find.text('Programme Analyst'), findsOneWidget);
  });
}

final class _HealthTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    expect(request.method, 'GET');
    expect(request.path, 'api/health');
    return {
      'status': 'ok',
      'open_jobs': 128,
      'enabled_sources': 12,
      'schema_version': '2026-07',
    };
  }
}

final class _FailingTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    throw const AtlasAPIException.http(503, 'job-api unavailable');
  }
}

final class _HealthAndSearchTransport implements AtlasTransport {
  @override
  Future<Object?> send(AtlasRequest request) async {
    switch (request.path) {
      case 'api/health':
        return {
          'status': 'ok',
          'open_jobs': 128,
          'enabled_sources': 12,
          'schema_version': '2026-07',
        };
      case 'api/search':
        expect(request.method, 'POST');
        return {
          'total': 1,
          'limit': request.jsonBody?['limit'] ?? 50,
          'offset': 0,
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
          'results': [
            {
              'job_key': 'undp_oracle_hcm:34063',
              'title': 'Programme Analyst',
              'organization': 'UNDP Oracle HCM',
              'source_id': 'undp_oracle_hcm',
              'duty_station': 'Nairobi, Kenya',
              'grade_code': 'P-3',
              'contract_group': 'Fixed term',
              'work_modality': 'Onsite',
              'closing_date': '2026-07-30T23:59:00Z',
              'needs_review': false,
              'score_reasons': <String>[],
              'match_summary': 'Matched current filters',
              'description': 'Role summary',
              'status': 'open',
            },
          ],
        };
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}
