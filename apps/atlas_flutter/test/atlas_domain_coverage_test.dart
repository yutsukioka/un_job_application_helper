import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('decodes saved searches, facets, sources, updates, and job details', () {
    final search = AtlasSavedSearch.fromJson({
      'name': 'nairobi',
      'description': 'Nairobi open roles',
      'request': {
        'text': 'programme',
        'status': ['open'],
        'source_ids': ['unicef_pageup'],
        'include_low_confidence': 'true',
        'include_facets': 0,
        'limit': '25',
        'offset': '5',
        'sort': 'closing_date_desc',
      },
      'created_at': '2026-07-01T00:00:00Z',
      'updated_at': '2026-07-02T00:00:00Z',
    });

    expect(search.name, 'nairobi');
    expect(search.request.includeLowConfidence, isTrue);
    expect(search.request.includeFacets, isFalse);
    expect(search.request.limit, 25);
    expect(search.request.offset, 5);
    expect(
      SortOrder.fromAPIValue(search.request.sort),
      SortOrder.deadlineLatest,
    );

    final response = AtlasSearchResponse.fromJson({
      'total': 0,
      'limit': 10,
      'offset': 0,
      'results': <Object?>[],
      'facets': {
        'organizations': {'UNICEF': '3'},
      },
      'facetLabels': {
        'organizations': {'UNICEF': 'United Nations Children Fund'},
      },
      'unclassifiedCount': '2',
    });

    expect(response.facets['organizations']?['UNICEF'], 3);
    expect(
      response.facetLabels['organizations']?['UNICEF'],
      'United Nations Children Fund',
    );
    expect(response.unclassifiedCount, 2);

    final source = AtlasSourceSummary.fromJson({
      'source_id': 'unicef_pageup',
      'organization': 'UNICEF',
      'total_jobs': 10,
      'open_jobs': 7,
      'last_seen_at': '2026-07-02',
      'health_status': 'ok',
      'observed_at': '2026-07-02T01:00:00Z',
      'detail_attempted': 8,
      'detail_failed': 1,
      'missing_transition_allowed': 1,
    });
    expect(source.sourceID, 'unicef_pageup');
    expect(source.missingTransitionAllowed, isTrue);

    final run = AtlasSourceRun.fromJson({
      'source_id': 'unicef_pageup',
      'fetched': 10,
      'inserted': 2,
      'updated': 3,
      'missing': 1,
      'closed': 4,
      'observed_at': '2026-07-02T01:00:00Z',
    });
    expect(run.closed, 4);

    final detail = AtlasJobDetail.fromJson({
      'job_key': 'unicef_pageup:593420',
      'title': 'Emergency Specialist',
      'description': 'Full details',
      'status': 'open',
      'closes_at': '2026-07-05T23:59:00Z',
      'closes_at_local': '2026-07-06 08:59',
      'closes_tz': 'Asia/Tokyo',
      'apply_url': 'https://example.org/apply',
      'source_url': 'https://example.org/source',
      'deadline_info': {
        'stored_utc': '2026-07-05T23:59:00Z',
        'source_local': '2026-07-05 23:59',
        'source_timezone': 'EAT',
        'source_text': 'Deadline midnight',
      },
      'display_sections': [
        {
          'title': 'Job Record',
          'body': 'Metadata',
          'rows': [
            {'label': 'Grade', 'value': 'P-3'},
          ],
        },
      ],
    });

    expect(detail.deadlineInfo?.sourceTimezone, 'EAT');
    expect(detail.displaySections.single.rows.single.value, 'P-3');
  });

  test(
    'covers deadline, organization, filter chips, sorting, and removals',
    () {
      final unknownOrganization = JobSearchResult(
        jobKey: 'unknown:1',
        title: 'Unknown',
        organization: 'pageup',
        sourceID: 'pageup',
        dutyStation: 'Unknown',
        gradeCode: 'G-5',
        contractLabel: 'Staff',
        workModality: 'Hybrid',
        closingDate: null,
        needsReview: false,
        scoreReasons: const [],
        matchSummary: 'Matched',
        description: 'Description',
      );
      expect(unknownOrganization.organizationDisplay, 'pageup');
      expect(unknownOrganization.deadlineUrgency(), DeadlineUrgency.unknown);
      expect(unknownOrganization.deadlineText(), 'No deadline');

      final passed = JobSearchResult(
        jobKey: 'expired:1',
        title: 'Expired',
        organization: 'UN',
        sourceID: 'un',
        dutyStation: 'Geneva',
        gradeCode: 'P-2',
        contractLabel: 'Staff',
        workModality: 'Onsite',
        closingDate: DateTime.utc(2026, 7, 1),
        needsReview: false,
        scoreReasons: const [],
        matchSummary: 'Matched',
        description: 'Description',
      );
      expect(
        passed.deadlineUrgency(now: DateTime.utc(2026, 7, 2)),
        DeadlineUrgency.passed,
      );
      expect(
        passed.deadlineText(now: DateTime.utc(2026, 7, 2)),
        'Deadline passed',
      );

      final neutral = JobSearchResult(
        jobKey: 'future:1',
        title: 'Future',
        organization: 'UNDP',
        sourceID: 'undp',
        dutyStation: 'Nairobi',
        gradeCode: 'P-4',
        contractLabel: 'Staff',
        workModality: 'Onsite',
        closingDate: DateTime.utc(2026, 8, 15),
        needsReview: false,
        scoreReasons: const [],
        matchSummary: 'Matched',
        description: 'Description',
      );
      expect(
        neutral.deadlineUrgency(now: DateTime.utc(2026, 7, 2)),
        DeadlineUrgency.neutral,
      );
      expect(
        neutral.deadlineText(now: DateTime.utc(2026, 7, 2)),
        'Closes Aug 15',
      );

      final soon = JobSearchResult(
        jobKey: 'soon:1',
        title: 'Soon',
        organization: 'UNDP',
        sourceID: 'undp',
        dutyStation: 'Nairobi',
        gradeCode: 'P-4',
        contractLabel: 'Staff',
        workModality: 'Onsite',
        closingDate: DateTime.utc(2026, 7, 8),
        needsReview: false,
        scoreReasons: const [],
        matchSummary: 'Matched',
        description: 'Description',
      );
      expect(soon.deadlineText(now: DateTime.utc(2026, 7, 2)), 'Closes in 6d');

      final filters = AtlasSearchFilters(
        openOnly: true,
        city: 'Nairobi',
        countryISO3: 'ken',
        scope: AtlasScopeFilter.national,
        closingSoon: true,
        gradeCodes: {'G5'},
        workModalities: {'hybrid'},
        sourceIDs: {'unicef_pageup', 'undp_oracle_hcm'},
        organizations: {'UNICEF', 'UNDP'},
        ccogFamilies: {'administration'},
        contractGroups: {'staff'},
        seniorityGroups: {'mid'},
        volunteerKinds: {'volunteer'},
        unvCategories: {'un_youth_volunteer'},
        capabilityTags: {'finance', 'data'},
        includeLowConfidence: true,
      );

      final chipIDs = filters.activeChips.map((chip) => chip.id).toSet();
      expect(
        chipIDs,
        containsAll({
          'deadline.soon',
          'location.country',
          'scope',
          'work.modalities',
          'organizations',
          'source.ids',
          'contract.groups',
          'volunteer.kinds',
          'unv.categories',
          'seniority.groups',
          'ccog.families',
          'capabilities',
          'confidence.low',
        }),
      );
      expect(filters.gradeSummary, 'G-5');
      expect(filters.workModalitySummary, 'Work: Hybrid');
      expect(
        filters.removingChip('confidence.low').includeLowConfidence,
        isFalse,
      );
      expect(filters.removingChip('volunteer.kinds').unvCategories, isEmpty);
      expect(filters.removingChip('unknown'), same(filters));
    },
  );

  test(
    'AtlasIOTransport sends JSON, query parameters, and HTTP errors',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(server.close);

      unawaited(
        server.forEach((request) async {
          if (request.uri.path == '/api/error') {
            request.response
              ..statusCode = 500
              ..write('broken');
            await request.response.close();
            return;
          }
          final body = await utf8.decoder.bind(request).join();
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({
              'method': request.method,
              'path': request.uri.path,
              'query': request.uri.queryParameters,
              'body': body.isEmpty ? null : jsonDecode(body),
            }),
          );
          await request.response.close();
        }),
      );

      final transport = AtlasIOTransport(
        baseURL: Uri.parse('http://127.0.0.1:${server.port}/'),
      );
      final response = await transport.send(
        const AtlasRequest(
          method: 'POST',
          path: 'api/search',
          queryParameters: {'probe': '1'},
          jsonBody: {'text': 'finance'},
        ),
      );
      final json = response as Map<String, Object?>;

      expect(json['method'], 'POST');
      expect(json['path'], '/api/search');
      expect(json['query'], {'probe': '1'});
      expect(json['body'], {'text': 'finance'});

      await expectLater(
        transport.send(const AtlasRequest(method: 'GET', path: 'api/error')),
        throwsA(
          isA<AtlasAPIException>().having(
            (error) => error.toString(),
            'message',
            contains('HTTP 500: broken'),
          ),
        ),
      );

      expect(
        const AtlasAPIException.http(404, '').toString(),
        'The server returned HTTP 404.',
      );
      expect(
        const AtlasAPIException.invalidResponse().toString(),
        'The server returned an invalid response.',
      );
    },
  );
}
