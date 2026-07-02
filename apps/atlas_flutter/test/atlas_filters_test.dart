import 'package:atlas/atlas.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('facet option stores id title and count for filter pills', () {
    const option = AtlasFacetOption(id: 'JPN', title: 'Japan', count: 42);

    expect(option.id, 'JPN');
    expect(option.title, 'Japan');
    expect(option.count, 42);
  });

  test('displayAtlasFilterValue matches Swift formatting', () {
    expect(
      displayAtlasFilterValue('consultant_contractor'),
      'Consultant Contractor',
    );
    expect(displayAtlasFilterValue('p3'), 'P3');
    expect(displayAtlasFilterValue('un'), 'UN');
    expect(displayAtlasFilterValue(''), 'Unknown');
  });

  test('seniority taxonomy matches iOS filter labels', () {
    expect(atlasSeniorityOrder.first, 'entry_junior');
    expect(atlasSeniorityLabels['entry_junior'], 'Entry Junior');
    expect(atlasSeniorityLabels['director_executive'], 'Director Executive');
    expect(
      atlasUNVCategoryInfo.map((info) => info.id),
      contains('un_youth_volunteer'),
    );
  });

  test('job display helpers match Atlas row behavior', () {
    final job = JobSearchResult(
      jobKey: 'undp_oracle_hcm:34063',
      title: 'Programme Analyst',
      organization: 'UNDP Oracle HCM',
      sourceID: 'undp_oracle_hcm',
      dutyStation: 'Nairobi, Kenya',
      gradeCode: 'P-3',
      contractLabel: 'Fixed term',
      workModality: 'Onsite',
      closingDate: DateTime.utc(2026, 7, 4, 12),
      needsReview: false,
      scoreReasons: const [],
      matchSummary: 'Cached row',
      description: 'Cached description',
    );

    expect(job.organizationDisplay, 'UNDP');
    expect(job.sourceInitials, 'UND');
    expect(
      job.deadlineUrgency(now: DateTime.utc(2026, 7, 2, 12)),
      DeadlineUrgency.critical,
    );
    expect(
      job.deadlineText(now: DateTime.utc(2026, 7, 2, 12)),
      'Closes in 48h',
    );
  });

  test('search filters expose active chips and removable state', () {
    final filters = AtlasSearchFilters(
      city: ' Nairobi ',
      countryISO3: 'ken',
      gradeCodes: {'P4', 'P2', 'P3'},
      workModalities: AtlasSearchFilters.remoteWorkModalities,
      capabilityTags: {'data'},
      capabilityQuery: 'reporting',
      includeLowConfidence: true,
    );

    expect(filters.activeChips.map((chip) => chip.id), contains('status.open'));
    expect(filters.gradeSummary, 'P-2 to P-4');
    expect(filters.workModalitySummary, 'Remote');
    expect(
      filters.activeChips.map((chip) => chip.id),
      contains('capabilities'),
    );

    final withoutCapabilities = filters.removingChip('capabilities');

    expect(withoutCapabilities.capabilityTags, isEmpty);
    expect(withoutCapabilities.capabilityQuery, isEmpty);
  });

  test('builds Swift-compatible search requests from filters', () {
    final filters = AtlasSearchFilters(
      city: ' Nairobi ',
      countryISO3: 'ken',
      scope: AtlasScopeFilter.international,
      includeLowConfidence: true,
      closingSoon: true,
      gradeCodes: {'P4', 'P2'},
      seniorityGroups: {'generic_volunteer', 'volunteer'},
      volunteerKinds: {AtlasVolunteerKind.volunteer.value},
      capabilityTags: {'data'},
      capabilityQuery: 'reporting, data',
      workModalities: {'home_based'},
    );

    final request = AtlasSearchRequest.fromFilters(
      filters: filters,
      query: ' cash ',
      sortOrder: SortOrder.newestPosted,
      limit: 200,
      now: DateTime.utc(2026, 7, 2, 9),
    );
    final json = request.toJson();

    expect(json['text'], 'cash');
    expect(json['status'], ['open']);
    expect(json['cities'], ['Nairobi']);
    expect(json['countries_iso3'], ['KEN']);
    expect(json['national_international'], ['international']);
    expect(json['grade_codes'], ['P2', 'P4']);
    expect(json['seniority_groups'], ['volunteer']);
    expect(json['volunteer_kinds'], ['un_volunteer', 'volunteer']);
    expect(json['capability_tags'], ['data', 'reporting']);
    expect(json['work_modalities'], ['home_based']);
    expect(json['closing_date_to'], '2026-07-09');
    expect(json['include_low_confidence'], isTrue);
    expect(json['include_facets'], isTrue);
    expect(json['limit'], 200);
    expect(json['offset'], 0);
    expect(json['sort'], 'posted_date_desc');
  });
}
