import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AtlasAPIClient URL handling', () {
    test('normalizes pasted health URL to base URL', () {
      final url = AtlasAPIClient.normalizedBaseURL(
        ' http://192.168.50.22:8765/api/health?probe=1#status ',
      );

      expect(url?.toString(), 'http://192.168.50.22:8765');
    });

    test('adds HTTP prefix for bare LAN host', () {
      final url = AtlasAPIClient.normalizedBaseURL('192.168.50.22:8765');

      expect(url?.toString(), 'http://192.168.50.22:8765');
    });

    test('rejects non-HTTP URLs', () {
      expect(AtlasAPIClient.normalizedBaseURL('ftp://example.org'), isNull);
    });
  });

  group('Atlas search response decoding', () {
    test('decodes cached camelCase search response rows', () {
      final response = AtlasSearchResponse.fromJson({
        'total': 1,
        'limit': 10000,
        'offset': 0,
        'facets': <String, Object?>{},
        'facet_labels': <String, Object?>{},
        'unclassified_count': 0,
        'results': [
          {
            'jobKey': 'undp_oracle_hcm:34063',
            'title': 'Programme Analyst',
            'organization': 'UNDP Oracle HCM',
            'sourceID': 'undp_oracle_hcm',
            'dutyStation': 'Nairobi, Kenya',
            'city': 'Nairobi',
            'countryISO3': 'KEN',
            'gradeCode': 'IPSA-9',
            'standardSeniorityTier': 'T3_MID_PROFESSIONAL',
            'contractLabel': 'Consultant',
            'workModality': 'Onsite',
            'closingDate': '2026-06-30T23:59:00Z',
            'needsReview': false,
            'scoreReasons': <String>[],
            'matchSummary': 'Cached row',
            'description': 'Cached description',
            'status': 'open',
          },
        ],
      });

      expect(response.total, 1);
      expect(response.results.single.jobKey, 'undp_oracle_hcm:34063');
      expect(response.results.single.city, 'Nairobi');
      expect(response.results.single.countryISO3, 'KEN');
      expect(response.results.single.gradeCode, 'IPSA-9');
      expect(
        response.results.single.standardSeniorityTier,
        'T3_MID_PROFESSIONAL',
      );
      expect(response.results.single.matchSummary, 'Cached row');
    });

    test('decodes snake_case API rows into display-ready jobs', () {
      final response = AtlasSearchResponse.fromJson({
        'total': 1,
        'limit': 50,
        'offset': 0,
        'facets': <String, Object?>{},
        'facet_labels': <String, Object?>{},
        'unclassified_count': 0,
        'results': [
          {
            'job_key': 'unicef_pageup:593420',
            'title': 'Emergency Specialist, P-3',
            'organization': 'UNICEF PageUp',
            'source_id': 'unicef_pageup',
            'duty_station': 'Nairobi, Kenya',
            'grade_code': 'p3',
            'standard_seniority_tier': 'T2_JUNIOR_PROFESSIONAL',
            'national_international': 'international',
            'contract_group': 'consultant_contractor',
            'work_modality': 'home_based',
            'closing_date': '2026-07-05T23:59:00Z',
            'posted_date': '2026-06-30',
            'status': 'open',
            'apply_url': 'https://example.org/apply',
            'source_url': 'https://example.org/source',
            'needs_review': true,
            'score': 86,
            'score_reasons': ['Term in title: cash based assistance'],
            'capability_tags': ['cash_based_assistance'],
            'match_evidence': {
              'location': {
                'matched_city': 'Nairobi',
                'matched_country_iso3': 'KEN',
                'source_field': 'title',
                'location_type': 'city',
                'confidence': 0.8,
              },
              'grade': {
                'matched_grade': 'p3',
                'source_field': 'title',
                'confidence': 0.9,
              },
              'scope': {'matched': 'international', 'reason': 'classification'},
            },
          },
        ],
      });

      final job = response.results.single;
      expect(job.jobKey, 'unicef_pageup:593420');
      expect(job.organization, 'UNICEF PageUp');
      expect(job.organizationDisplay, 'UNICEF');
      expect(job.sourceInitials, 'UNI');
      expect(job.city, 'Nairobi');
      expect(job.countryISO3, 'KEN');
      expect(job.gradeCode, 'P-3');
      expect(job.standardSeniorityTier, 'T2_JUNIOR_PROFESSIONAL');
      expect(job.contractLabel, 'Consultant Contractor');
      expect(job.workModality, 'HOME Based');
      expect(job.score, 0.86);
      expect(job.locationConfidence, 0.8);
      expect(job.gradeConfidence, 0.9);
      expect(job.matchSummary, contains('Location matched Nairobi, Kenya'));
      expect(job.matchSummary, contains('Grade P-3 matched'));
      expect(job.applyURL?.toString(), 'https://example.org/apply');
      expect(job.sourceURL?.toString(), 'https://example.org/source');
    });
  });

  group('AtlasAPIClient transport routing', () {
    test('uses Swift-compatible methods, paths, query, and bodies', () async {
      final transport = RecordingAtlasTransport();
      final client = AtlasAPIClient(
        baseURL: Uri.parse('http://127.0.0.1:8765'),
        transport: transport,
      );

      await client.health();
      await client.search(const AtlasSearchRequest(text: 'finance', limit: 25));
      await client.jobDetail('unicef_pageup:593420');
      await client.savedSearches();
      await client.saveSearch(
        name: 'finance',
        request: const AtlasSearchRequest(text: 'finance'),
        summary: 'Text: finance',
      );
      await client.deleteSavedSearch('finance');
      await client.saveJob('unicef_pageup:593420');
      await client.trackerRecords();
      await client.deleteTrackerRecord('unicef_pageup-593420');
      await client.updates();
      await client.sources();

      expect(transport.requests.map((request) => request.method), [
        'GET',
        'POST',
        'GET',
        'GET',
        'POST',
        'DELETE',
        'POST',
        'GET',
        'DELETE',
        'GET',
        'GET',
      ]);
      expect(transport.requests.map((request) => request.path), [
        'api/health',
        'api/search',
        'api/job-detail',
        'api/saved-searches',
        'api/saved-searches',
        'api/saved-searches/finance',
        'api/tracker/jobs/unicef_pageup%3A593420',
        'api/tracker',
        'api/tracker/unicef_pageup-593420',
        'api/updates',
        'api/sources',
      ]);
      expect(transport.requests[2].queryParameters, {
        'job_key': 'unicef_pageup:593420',
      });
      expect(transport.requests[1].jsonBody?['text'], 'finance');
      expect(transport.requests[1].jsonBody?['limit'], 25);
      expect(transport.requests[4].jsonBody?['name'], 'finance');
      expect(transport.requests[4].jsonBody?['summary'], 'Text: finance');
      expect(transport.requests[4].jsonBody?['request'], isA<Map>());
    });
  });
}

final class RecordingAtlasTransport implements AtlasTransport {
  final requests = <AtlasRequest>[];

  @override
  Future<Object?> send(AtlasRequest request) async {
    requests.add(request);
    switch (request.path) {
      case 'api/health':
        return {'status': 'ok', 'open_jobs': 1, 'enabled_sources': 1};
      case 'api/search':
        return {
          'total': 0,
          'limit': request.jsonBody?['limit'] ?? 50,
          'offset': 0,
          'results': <Object?>[],
          'facets': <String, Object?>{},
          'facet_labels': <String, Object?>{},
          'unclassified_count': 0,
        };
      case 'api/job-detail':
        return {
          'job_key': request.queryParameters['job_key'],
          'display_sections': <Object?>[],
        };
      case 'api/saved-searches':
        if (request.method == 'POST') {
          return {
            'name': request.jsonBody?['name'],
            'description': request.jsonBody?['summary'],
            'request': request.jsonBody?['request'],
          };
        }
        return <Object?>[];
      case 'api/saved-searches/finance':
      case 'api/tracker/unicef_pageup-593420':
        return {'deleted': true};
      case 'api/tracker/jobs/unicef_pageup%3A593420':
        return {
          'id': 'unicef_pageup-593420',
          'job_key': 'unicef_pageup:593420',
          'status': 'saved',
        };
      case 'api/tracker':
        return <Object?>[];
      case 'api/updates':
        return {'recent_source_runs': <Object?>[]};
      case 'api/sources':
        return {'sources': <Object?>[]};
      default:
        fail('Unexpected request ${request.method} ${request.path}');
    }
  }
}
