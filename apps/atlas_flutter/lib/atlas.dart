import 'dart:async';
import 'dart:convert';
import 'dart:io';

enum DeadlineUrgency { neutral, soon, critical, passed, unknown }

enum SortOrder {
  closingSoon('Closing soon', 'closing_date_asc'),
  newestPosted('Newest posted', 'posted_date_desc'),
  deadlineLatest('Deadline latest', 'closing_date_desc'),
  bestFit('Best fit', 'closing_date_asc');

  const SortOrder(this.label, this.apiValue);

  final String label;
  final String apiValue;

  static SortOrder fromAPIValue(String value) {
    return switch (value) {
      'posted_date_desc' => SortOrder.newestPosted,
      'closing_date_desc' => SortOrder.deadlineLatest,
      _ => SortOrder.closingSoon,
    };
  }
}

enum AtlasScopeFilter {
  any('Any', <String>[]),
  international('International', <String>['international']),
  national('National', <String>['national', 'local']),
  unspecified('Unspecified', <String>['unknown']);

  const AtlasScopeFilter(this.title, this.apiValues);

  final String title;
  final List<String> apiValues;
}

enum AtlasVolunteerKind {
  unVolunteer('un_volunteer', 'UN Volunteer'),
  volunteer('volunteer', 'Volunteer');

  const AtlasVolunteerKind(this.value, this.title);

  final String value;
  final String title;
}

final class AtlasActiveFilterChip {
  const AtlasActiveFilterChip({required this.id, required this.title});

  final String id;
  final String title;

  @override
  bool operator ==(Object other) {
    return other is AtlasActiveFilterChip &&
        other.id == id &&
        other.title == title;
  }

  @override
  int get hashCode => Object.hash(id, title);
}

final class AtlasFacetOption {
  const AtlasFacetOption({
    required this.id,
    required this.title,
    required this.count,
  });

  final String id;
  final String title;
  final int count;
}

final class JobSearchResult {
  JobSearchResult({
    required this.jobKey,
    required this.title,
    required this.organization,
    required this.sourceID,
    required this.dutyStation,
    required this.gradeCode,
    this.nationalInternational,
    this.contractCategory,
    this.contractGroup,
    this.seniorityGroup,
    required this.contractLabel,
    required this.workModality,
    this.ccogFamilyCode,
    this.ccogFamilyLabel,
    this.ccogPrimaryCode,
    this.ccogPrimaryLabel,
    this.capabilityTags = const <String>[],
    required this.closingDate,
    required this.needsReview,
    this.locationConfidence,
    this.gradeConfidence,
    this.score,
    required this.scoreReasons,
    required this.matchSummary,
    required this.description,
    this.status = 'open',
    this.postedDate,
    this.applyURL,
    this.sourceURL,
  });

  factory JobSearchResult.fromJson(Map<String, Object?> json) {
    if (json.containsKey('job_key')) {
      return JobSearchResult.fromAPIJson(json);
    }
    return JobSearchResult(
      jobKey: _string(json['jobKey']) ?? '',
      title: _string(json['title']) ?? 'Untitled vacancy',
      organization: _string(json['organization']) ?? 'Unknown organization',
      sourceID: _string(json['sourceID']) ?? 'unknown',
      dutyStation: _string(json['dutyStation']) ?? 'Location not classified',
      gradeCode: _string(json['gradeCode']) ?? '',
      nationalInternational: _string(json['nationalInternational']),
      contractCategory: _string(json['contractCategory']),
      contractGroup: _string(json['contractGroup']),
      seniorityGroup: _string(json['seniorityGroup']),
      contractLabel: _string(json['contractLabel']) ?? 'Contract unknown',
      workModality: _string(json['workModality']) ?? 'Modality unknown',
      ccogFamilyCode: _string(json['ccogFamilyCode']),
      ccogFamilyLabel: _string(json['ccogFamilyLabel']),
      ccogPrimaryCode: _string(json['ccogPrimaryCode']),
      ccogPrimaryLabel: _string(json['ccogPrimaryLabel']),
      capabilityTags: _stringList(json['capabilityTags']),
      closingDate: _date(json['closingDate']),
      needsReview: _bool(json['needsReview']) ?? false,
      locationConfidence: _double(json['locationConfidence']),
      gradeConfidence: _double(json['gradeConfidence']),
      score: _normalizedScore(_double(json['score'])),
      scoreReasons: _stringList(json['scoreReasons']),
      matchSummary:
          _string(json['matchSummary']) ??
          'Matched the current search filters.',
      description: _string(json['description']) ?? '',
      status: _string(json['status']) ?? 'open',
      postedDate: _date(json['postedDate']),
      applyURL: _uri(json['applyURL']),
      sourceURL: _uri(json['sourceURL']),
    );
  }

  factory JobSearchResult.fromAPIJson(Map<String, Object?> json) {
    final organizationLabel = _displayLabel(
      _string(json['organization']),
      fallback: 'Unknown organization',
    );
    final source = _string(json['source_id']) ?? 'unknown';
    final station = _displayLabel(
      _string(json['duty_station']),
      fallback: 'Location not classified',
    );
    final grade = _displayGradeOptional(
      _string(json['grade_code']) ?? _string(json['grade_family']),
    );
    final contract = _displayLabel(
      _string(json['contract_group']) ??
          _string(json['contract_category']) ??
          _string(json['seniority_group']),
      fallback: 'Contract unknown',
    );
    final modality = _displayLabel(
      _string(json['work_modality']),
      fallback: 'Modality unknown',
    );
    final matchEvidence = _map(json['match_evidence']);
    final location = _map(matchEvidence?['location']);
    final gradeEvidence = _map(matchEvidence?['grade']);
    final scope = _map(matchEvidence?['scope']);
    final summary = _matchSummary(
      location: location,
      grade: gradeEvidence,
      scope: scope,
      dutyStation: station,
    );
    final title = _string(json['title'])?.trim();
    final cleanTitle = title == null || title.isEmpty
        ? 'Untitled vacancy'
        : title;

    return JobSearchResult(
      jobKey: _string(json['job_key']) ?? '',
      title: cleanTitle,
      organization: organizationLabel,
      sourceID: source,
      dutyStation: station,
      gradeCode: grade,
      nationalInternational: _string(json['national_international']),
      contractCategory: _string(json['contract_category']),
      contractGroup: _string(json['contract_group']),
      seniorityGroup: _string(json['seniority_group']),
      contractLabel: contract,
      workModality: modality,
      ccogFamilyCode: _string(json['ccog_family_code']),
      ccogFamilyLabel: _string(json['ccog_family_label']),
      ccogPrimaryCode: _string(json['ccog_primary_code']),
      ccogPrimaryLabel: _string(json['ccog_primary_label']),
      capabilityTags: _stringList(json['capability_tags']),
      closingDate: _date(json['closing_date']),
      needsReview: _bool(json['needs_review']) ?? false,
      locationConfidence: _double(location?['confidence']),
      gradeConfidence: _double(gradeEvidence?['confidence']),
      score: _normalizedScore(_double(json['score'])),
      scoreReasons: _stringList(json['score_reasons']),
      matchSummary: summary,
      description: _descriptionFallback(
        title: cleanTitle,
        organization: organizationLabel,
        dutyStation: station,
        contract: contract,
        modality: modality,
      ),
      status: _string(json['status']) ?? 'unknown',
      postedDate: _date(json['posted_date']),
      applyURL: _uri(json['apply_url']),
      sourceURL: _uri(json['source_url']),
    );
  }

  final String jobKey;
  final String title;
  final String organization;
  final String sourceID;
  final String dutyStation;
  final String gradeCode;
  final String? nationalInternational;
  final String? contractCategory;
  final String? contractGroup;
  final String? seniorityGroup;
  final String contractLabel;
  final String workModality;
  final String? ccogFamilyCode;
  final String? ccogFamilyLabel;
  final String? ccogPrimaryCode;
  final String? ccogPrimaryLabel;
  final List<String> capabilityTags;
  final DateTime? closingDate;
  final bool needsReview;
  final double? locationConfidence;
  final double? gradeConfidence;
  final double? score;
  final List<String> scoreReasons;
  final String matchSummary;
  final String description;
  final String status;
  final DateTime? postedDate;
  final Uri? applyURL;
  final Uri? sourceURL;

  String get organizationDisplay {
    const atsTokens = <String>{
      'pageup',
      'successfactors',
      'taleo',
      'workday',
      'inspira',
      'avature',
      'csod',
      'recruitee',
      'smartrecruiters',
      'oracle',
      'hcm',
      'peoplesoft',
      'talentsoft',
      'uvp',
      'api',
      'static',
      'html',
      'custom',
      'legacy',
      'rmk',
      'drupal',
      'split',
    };
    final words = organization
        .split(RegExp(r'\s+'))
        .where((word) => word.isNotEmpty)
        .where((word) => !atsTokens.contains(word.toLowerCase()))
        .toList();
    if (words.isEmpty) {
      return organization;
    }
    final cleaned = words.join(' ');
    if (words.length == 1 && cleaned.length <= 6) {
      return cleaned.toUpperCase();
    }
    return cleaned;
  }

  String get sourceInitials {
    final initials = organizationDisplay
        .split(RegExp(r'\s+'))
        .where((word) => word.isNotEmpty)
        .take(3)
        .map((word) => word[0])
        .join()
        .toUpperCase();
    if (initials.length >= 2) {
      return initials;
    }
    final end = organizationDisplay.length < 3 ? organizationDisplay.length : 3;
    return organizationDisplay.substring(0, end).toUpperCase();
  }

  DeadlineUrgency deadlineUrgency({DateTime? now}) {
    final closing = closingDate;
    if (closing == null) {
      return DeadlineUrgency.unknown;
    }
    final hours =
        closing.difference(now ?? DateTime.now()).inMilliseconds /
        Duration.millisecondsPerHour;
    if (hours < 0) {
      return DeadlineUrgency.passed;
    }
    if (hours <= 48) {
      return DeadlineUrgency.critical;
    }
    if (hours <= 24 * 7) {
      return DeadlineUrgency.soon;
    }
    return DeadlineUrgency.neutral;
  }

  String deadlineText({DateTime? now}) {
    final closing = closingDate;
    if (closing == null) {
      return 'No deadline';
    }
    final hours =
        closing.difference(now ?? DateTime.now()).inMilliseconds /
        Duration.millisecondsPerHour;
    if (hours < 0) {
      return 'Deadline passed';
    }
    if (hours <= 48) {
      return 'Closes in ${hours.ceil().clamp(1, 1 << 30)}h';
    }
    if (hours <= 24 * 14) {
      return 'Closes in ${(hours / 24).ceil().clamp(1, 1 << 30)}d';
    }
    return 'Closes ${_monthAbbreviation(closing.month)} ${closing.day}';
  }
}

final class AtlasSearchFilters {
  AtlasSearchFilters({
    this.openOnly = true,
    this.city = '',
    this.countryISO3 = '',
    this.scope = AtlasScopeFilter.any,
    this.includeLowConfidence = false,
    this.closingSoon = false,
    Set<String>? gradeCodes,
    Set<String>? workModalities,
    Set<String>? sourceIDs,
    Set<String>? organizations,
    Set<String>? ccogFamilies,
    Set<String>? contractGroups,
    Set<String>? seniorityGroups,
    Set<String>? volunteerKinds,
    Set<String>? unvCategories,
    Set<String>? unvVolunteerTypes,
    Set<String>? capabilityTags,
    this.capabilityQuery = '',
  }) : gradeCodes = Set.unmodifiable(gradeCodes ?? const <String>{}),
       workModalities = Set.unmodifiable(workModalities ?? const <String>{}),
       sourceIDs = Set.unmodifiable(sourceIDs ?? const <String>{}),
       organizations = Set.unmodifiable(organizations ?? const <String>{}),
       ccogFamilies = Set.unmodifiable(ccogFamilies ?? const <String>{}),
       contractGroups = Set.unmodifiable(contractGroups ?? const <String>{}),
       seniorityGroups = Set.unmodifiable(seniorityGroups ?? const <String>{}),
       volunteerKinds = Set.unmodifiable(volunteerKinds ?? const <String>{}),
       unvCategories = Set.unmodifiable(unvCategories ?? const <String>{}),
       unvVolunteerTypes = Set.unmodifiable(
         unvVolunteerTypes ?? const <String>{},
       ),
       capabilityTags = Set.unmodifiable(capabilityTags ?? const <String>{});

  static const remoteWorkModalities = <String>{'home_based', 'online_remote'};

  final bool openOnly;
  final String city;
  final String countryISO3;
  final AtlasScopeFilter scope;
  final bool includeLowConfidence;
  final bool closingSoon;
  final Set<String> gradeCodes;
  final Set<String> workModalities;
  final Set<String> sourceIDs;
  final Set<String> organizations;
  final Set<String> ccogFamilies;
  final Set<String> contractGroups;
  final Set<String> seniorityGroups;
  final Set<String> volunteerKinds;
  final Set<String> unvCategories;
  final Set<String> unvVolunteerTypes;
  final Set<String> capabilityTags;
  final String capabilityQuery;

  String get trimmedCity => city.trim();

  String get trimmedCountryISO3 => countryISO3.trim();

  String get trimmedCapabilityQuery => capabilityQuery.trim();

  bool get isRemoteOnly => _setEquals(workModalities, remoteWorkModalities);

  List<String> get capabilityTerms {
    final terms = <String>{
      ...trimmedCapabilityQuery
          .split(',')
          .map((value) => value.trim())
          .where((value) => value.isNotEmpty),
      ...capabilityTags,
    }.toList()..sort();
    return terms;
  }

  List<String> get sortedGradeCodes {
    final values = gradeCodes.toList()
      ..sort(
        (left, right) => _gradeSortKey(left).compareTo(_gradeSortKey(right)),
      );
    return values;
  }

  String get gradeSummary {
    final sorted = sortedGradeCodes;
    if (_listEquals(sorted, const <String>['P2', 'P3', 'P4'])) {
      return 'P-2 to P-4';
    }
    return sorted.map((value) => _displayGrade(value)).join(', ');
  }

  String get workModalitySummary {
    if (isRemoteOnly) {
      return 'Remote';
    }
    return _selectionSummary(prefix: 'Work', values: workModalities);
  }

  List<AtlasActiveFilterChip> get activeChips {
    final chips = <AtlasActiveFilterChip>[];
    if (openOnly) {
      chips.add(
        const AtlasActiveFilterChip(id: 'status.open', title: 'Open only'),
      );
    }
    if (closingSoon) {
      chips.add(
        const AtlasActiveFilterChip(id: 'deadline.soon', title: 'Closing soon'),
      );
    }
    if (trimmedCity.isNotEmpty) {
      chips.add(AtlasActiveFilterChip(id: 'location.city', title: trimmedCity));
    }
    if (trimmedCountryISO3.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'location.country',
          title: trimmedCountryISO3.toUpperCase(),
        ),
      );
    }
    if (scope != AtlasScopeFilter.any) {
      chips.add(
        AtlasActiveFilterChip(id: 'scope', title: 'Scope: ${scope.title}'),
      );
    }
    if (gradeCodes.isNotEmpty) {
      chips.add(AtlasActiveFilterChip(id: 'grade.codes', title: gradeSummary));
    }
    if (workModalities.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'work.modalities',
          title: workModalitySummary,
        ),
      );
    }
    if (organizations.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'organizations',
          title: _selectionSummary(prefix: 'Org', values: organizations),
        ),
      );
    }
    if (sourceIDs.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'source.ids',
          title: _selectionSummary(prefix: 'Source', values: sourceIDs),
        ),
      );
    }
    if (contractGroups.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'contract.groups',
          title: _selectionSummary(prefix: 'Contract', values: contractGroups),
        ),
      );
    }
    if (volunteerKinds.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'volunteer.kinds',
          title: _selectionSummary(prefix: 'Volunteer', values: volunteerKinds),
        ),
      );
    }
    if (unvCategories.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'unv.categories',
          title: _selectionSummary(prefix: 'UNV', values: unvCategories),
        ),
      );
    }
    if (seniorityGroups.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'seniority.groups',
          title: _selectionSummary(
            prefix: 'Seniority',
            values: seniorityGroups,
          ),
        ),
      );
    }
    if (ccogFamilies.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'ccog.families',
          title: _selectionSummary(prefix: 'CCOG', values: ccogFamilies),
        ),
      );
    }
    if (trimmedCapabilityQuery.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'capabilities',
          title: 'Skill: $trimmedCapabilityQuery',
        ),
      );
    } else if (capabilityTags.isNotEmpty) {
      chips.add(
        AtlasActiveFilterChip(
          id: 'capabilities',
          title: _selectionSummary(prefix: 'Skill', values: capabilityTags),
        ),
      );
    }
    if (includeLowConfidence) {
      chips.add(
        const AtlasActiveFilterChip(
          id: 'confidence.low',
          title: 'Include uncertain',
        ),
      );
    }
    return chips;
  }

  AtlasSearchFilters copyWith({
    bool? openOnly,
    String? city,
    String? countryISO3,
    AtlasScopeFilter? scope,
    bool? includeLowConfidence,
    bool? closingSoon,
    Set<String>? gradeCodes,
    Set<String>? workModalities,
    Set<String>? sourceIDs,
    Set<String>? organizations,
    Set<String>? ccogFamilies,
    Set<String>? contractGroups,
    Set<String>? seniorityGroups,
    Set<String>? volunteerKinds,
    Set<String>? unvCategories,
    Set<String>? unvVolunteerTypes,
    Set<String>? capabilityTags,
    String? capabilityQuery,
  }) {
    return AtlasSearchFilters(
      openOnly: openOnly ?? this.openOnly,
      city: city ?? this.city,
      countryISO3: countryISO3 ?? this.countryISO3,
      scope: scope ?? this.scope,
      includeLowConfidence: includeLowConfidence ?? this.includeLowConfidence,
      closingSoon: closingSoon ?? this.closingSoon,
      gradeCodes: gradeCodes ?? this.gradeCodes,
      workModalities: workModalities ?? this.workModalities,
      sourceIDs: sourceIDs ?? this.sourceIDs,
      organizations: organizations ?? this.organizations,
      ccogFamilies: ccogFamilies ?? this.ccogFamilies,
      contractGroups: contractGroups ?? this.contractGroups,
      seniorityGroups: seniorityGroups ?? this.seniorityGroups,
      volunteerKinds: volunteerKinds ?? this.volunteerKinds,
      unvCategories: unvCategories ?? this.unvCategories,
      unvVolunteerTypes: unvVolunteerTypes ?? this.unvVolunteerTypes,
      capabilityTags: capabilityTags ?? this.capabilityTags,
      capabilityQuery: capabilityQuery ?? this.capabilityQuery,
    );
  }

  AtlasSearchFilters removingChip(String id) {
    return switch (id) {
      'status.open' => copyWith(openOnly: false),
      'deadline.soon' => copyWith(closingSoon: false),
      'location.city' => copyWith(city: ''),
      'location.country' => copyWith(countryISO3: ''),
      'scope' => copyWith(scope: AtlasScopeFilter.any),
      'grade.codes' => copyWith(gradeCodes: <String>{}),
      'work.modalities' => copyWith(workModalities: <String>{}),
      'organizations' => copyWith(organizations: <String>{}),
      'source.ids' => copyWith(sourceIDs: <String>{}),
      'contract.groups' => copyWith(contractGroups: <String>{}),
      'volunteer.kinds' => copyWith(
        volunteerKinds: <String>{},
        unvCategories: <String>{},
        unvVolunteerTypes: <String>{},
      ),
      'unv.categories' => copyWith(unvCategories: <String>{}),
      'seniority.groups' => copyWith(seniorityGroups: <String>{}),
      'ccog.families' => copyWith(ccogFamilies: <String>{}),
      'capabilities' => copyWith(
        capabilityTags: <String>{},
        capabilityQuery: '',
      ),
      'confidence.low' => copyWith(includeLowConfidence: false),
      _ => this,
    };
  }
}

final class AtlasSearchRequest {
  const AtlasSearchRequest({
    this.text,
    this.status = const <String>['open'],
    this.organizations = const <String>[],
    this.sourceIDs = const <String>[],
    this.cities = const <String>[],
    this.countriesISO3 = const <String>[],
    this.nationalInternational = const <String>[],
    this.gradeCodes = const <String>[],
    this.ccogFamilies = const <String>[],
    this.capabilityTags = const <String>[],
    this.contractGroups = const <String>[],
    this.seniorityGroups = const <String>[],
    this.workModalities = const <String>[],
    this.volunteerKinds = const <String>[],
    this.unvCategories = const <String>[],
    this.unvVolunteerTypes = const <String>[],
    this.closingDateTo,
    this.includeLowConfidence = false,
    this.includeFacets = true,
    this.limit = 50,
    this.offset = 0,
    this.sort = 'closing_date_asc',
  });

  factory AtlasSearchRequest.fromJson(Map<String, Object?> json) {
    return AtlasSearchRequest(
      text: _string(json['text']),
      status: _stringList(json['status'], fallback: const <String>['open']),
      organizations: _stringList(json['organizations']),
      sourceIDs: _stringList(json['source_ids']),
      cities: _stringList(json['cities']),
      countriesISO3: _stringList(json['countries_iso3']),
      nationalInternational: _stringList(json['national_international']),
      gradeCodes: _stringList(json['grade_codes']),
      ccogFamilies: _stringList(json['ccog_families']),
      capabilityTags: _stringList(json['capability_tags']),
      contractGroups: _stringList(json['contract_groups']),
      seniorityGroups: _stringList(json['seniority_groups']),
      workModalities: _stringList(json['work_modalities']),
      volunteerKinds: _stringList(json['volunteer_kinds']),
      unvCategories: _stringList(json['unv_categories']),
      unvVolunteerTypes: _stringList(json['unv_volunteer_types']),
      closingDateTo: _string(json['closing_date_to']),
      includeLowConfidence: _bool(json['include_low_confidence']) ?? false,
      includeFacets: _bool(json['include_facets']) ?? true,
      limit: _int(json['limit']) ?? 50,
      offset: _int(json['offset']) ?? 0,
      sort: _string(json['sort']) ?? 'closing_date_asc',
    );
  }

  factory AtlasSearchRequest.fromFilters({
    required AtlasSearchFilters filters,
    required String query,
    required SortOrder sortOrder,
    required int limit,
    DateTime? now,
  }) {
    final trimmedQuery = query.trim();
    final city = filters.trimmedCity;
    final country = filters.trimmedCountryISO3.toUpperCase();
    return AtlasSearchRequest(
      text: trimmedQuery.isEmpty ? null : trimmedQuery,
      status: filters.openOnly ? const <String>['open'] : const <String>[],
      organizations: _sorted(filters.organizations),
      sourceIDs: _sorted(filters.sourceIDs),
      cities: city.isEmpty ? const <String>[] : <String>[city],
      countriesISO3: country.isEmpty ? const <String>[] : <String>[country],
      nationalInternational: filters.scope.apiValues,
      gradeCodes: filters.sortedGradeCodes,
      ccogFamilies: _sorted(filters.ccogFamilies),
      capabilityTags: filters.capabilityTerms,
      contractGroups: _sorted(filters.contractGroups),
      seniorityGroups: _backendSeniorityGroups(filters),
      workModalities: _sorted(filters.workModalities),
      volunteerKinds: _backendVolunteerKinds(filters),
      unvCategories: _sorted(filters.unvCategories),
      unvVolunteerTypes: _sorted(filters.unvVolunteerTypes),
      closingDateTo: filters.closingSoon
          ? _dateOnly((now ?? DateTime.now()).add(const Duration(days: 7)))
          : null,
      includeLowConfidence: filters.includeLowConfidence,
      includeFacets: true,
      limit: limit,
      offset: 0,
      sort: sortOrder.apiValue,
    );
  }

  final String? text;
  final List<String> status;
  final List<String> organizations;
  final List<String> sourceIDs;
  final List<String> cities;
  final List<String> countriesISO3;
  final List<String> nationalInternational;
  final List<String> gradeCodes;
  final List<String> ccogFamilies;
  final List<String> capabilityTags;
  final List<String> contractGroups;
  final List<String> seniorityGroups;
  final List<String> workModalities;
  final List<String> volunteerKinds;
  final List<String> unvCategories;
  final List<String> unvVolunteerTypes;
  final String? closingDateTo;
  final bool includeLowConfidence;
  final bool includeFacets;
  final int limit;
  final int offset;
  final String sort;

  Map<String, Object?> toJson() {
    final json = <String, Object?>{
      'status': status,
      'organizations': organizations,
      'source_ids': sourceIDs,
      'cities': cities,
      'countries_iso3': countriesISO3,
      'national_international': nationalInternational,
      'grade_codes': gradeCodes,
      'ccog_families': ccogFamilies,
      'capability_tags': capabilityTags,
      'contract_groups': contractGroups,
      'seniority_groups': seniorityGroups,
      'work_modalities': workModalities,
      'volunteer_kinds': volunteerKinds,
      'unv_categories': unvCategories,
      'unv_volunteer_types': unvVolunteerTypes,
      'include_low_confidence': includeLowConfidence,
      'include_facets': includeFacets,
      'limit': limit,
      'offset': offset,
      'sort': sort,
    };
    if (text != null) {
      json['text'] = text;
    }
    if (closingDateTo != null) {
      json['closing_date_to'] = closingDateTo;
    }
    return json;
  }
}

final class AtlasSearchResponse {
  AtlasSearchResponse({
    required this.total,
    required this.limit,
    required this.offset,
    required this.results,
    required this.facets,
    required this.facetLabels,
    required this.unclassifiedCount,
  });

  factory AtlasSearchResponse.fromJson(Map<String, Object?> json) {
    final rawResults = _list(json['results']);
    return AtlasSearchResponse(
      total: _int(json['total']) ?? 0,
      limit: _int(json['limit']) ?? 0,
      offset: _int(json['offset']) ?? 0,
      results: rawResults
          .map(_map)
          .nonNulls
          .map(JobSearchResult.fromJson)
          .toList(),
      facets: _facetMap(json['facets']),
      facetLabels: _facetLabelMap(json['facet_labels'] ?? json['facetLabels']),
      unclassifiedCount:
          _int(json['unclassified_count'] ?? json['unclassifiedCount']) ?? 0,
    );
  }

  final int total;
  final int limit;
  final int offset;
  final List<JobSearchResult> results;
  final Map<String, Map<String, int>> facets;
  final Map<String, Map<String, String>> facetLabels;
  final int unclassifiedCount;
}

final class AtlasHealthSummary {
  AtlasHealthSummary({
    required this.status,
    this.dbPath,
    this.schemaVersion,
    this.openJobs,
    this.enabledSources,
    this.lastSyncAt,
  });

  factory AtlasHealthSummary.fromJson(Map<String, Object?> json) {
    return AtlasHealthSummary(
      status: _string(json['status']) ?? 'unknown',
      dbPath: _string(json['db_path']),
      schemaVersion: _string(json['schema_version']),
      openJobs: _int(json['open_jobs']),
      enabledSources: _int(json['enabled_sources']),
      lastSyncAt: _string(json['last_sync_at']),
    );
  }

  final String status;
  final String? dbPath;
  final String? schemaVersion;
  final int? openJobs;
  final int? enabledSources;
  final String? lastSyncAt;
}

final class AtlasSavedSearch {
  AtlasSavedSearch({
    required this.name,
    this.description,
    required this.request,
    this.createdAt,
    this.updatedAt,
  });

  factory AtlasSavedSearch.fromJson(Map<String, Object?> json) {
    return AtlasSavedSearch(
      name: _string(json['name']) ?? '',
      description: _string(json['description']),
      request: AtlasSearchRequest.fromJson(
        _map(json['request']) ?? const <String, Object?>{},
      ),
      createdAt: _string(json['created_at']),
      updatedAt: _string(json['updated_at']),
    );
  }

  final String name;
  final String? description;
  final AtlasSearchRequest request;
  final String? createdAt;
  final String? updatedAt;
}

final class AtlasApplicationRecord {
  AtlasApplicationRecord({
    required this.id,
    required this.jobKey,
    required this.status,
    this.notes,
    this.appliedAt,
    this.updatedAt,
  });

  factory AtlasApplicationRecord.fromJson(Map<String, Object?> json) {
    return AtlasApplicationRecord(
      id: _string(json['id']) ?? '',
      jobKey: _string(json['job_key']) ?? '',
      status: _string(json['status']) ?? 'saved',
      notes: _string(json['notes']),
      appliedAt: _string(json['applied_at']),
      updatedAt: _string(json['updated_at']),
    );
  }

  final String id;
  final String jobKey;
  final String status;
  final String? notes;
  final String? appliedAt;
  final String? updatedAt;
}

final class AtlasSourceSummary {
  AtlasSourceSummary({
    required this.sourceID,
    required this.organization,
    required this.totalJobs,
    required this.openJobs,
    this.lastSeenAt,
    this.healthStatus,
    this.observedAt,
    this.detailAttempted,
    this.detailFailed,
    this.missingTransitionAllowed,
  });

  factory AtlasSourceSummary.fromJson(Map<String, Object?> json) {
    return AtlasSourceSummary(
      sourceID: _string(json['source_id']) ?? '',
      organization: _string(json['organization']) ?? '',
      totalJobs: _int(json['total_jobs']) ?? 0,
      openJobs: _int(json['open_jobs']) ?? 0,
      lastSeenAt: _string(json['last_seen_at']),
      healthStatus: _string(json['health_status']),
      observedAt: _string(json['observed_at']),
      detailAttempted: _int(json['detail_attempted']),
      detailFailed: _int(json['detail_failed']),
      missingTransitionAllowed: _bool(json['missing_transition_allowed']),
    );
  }

  final String sourceID;
  final String organization;
  final int totalJobs;
  final int openJobs;
  final String? lastSeenAt;
  final String? healthStatus;
  final String? observedAt;
  final int? detailAttempted;
  final int? detailFailed;
  final bool? missingTransitionAllowed;
}

final class AtlasSourceRun {
  AtlasSourceRun({
    required this.sourceID,
    required this.fetched,
    required this.inserted,
    required this.updated,
    required this.missing,
    required this.closed,
    this.observedAt,
  });

  factory AtlasSourceRun.fromJson(Map<String, Object?> json) {
    return AtlasSourceRun(
      sourceID: _string(json['source_id']) ?? '',
      fetched: _int(json['fetched']) ?? 0,
      inserted: _int(json['inserted']) ?? 0,
      updated: _int(json['updated']) ?? 0,
      missing: _int(json['missing']) ?? 0,
      closed: _int(json['closed']) ?? 0,
      observedAt: _string(json['observed_at']),
    );
  }

  final String sourceID;
  final int fetched;
  final int inserted;
  final int updated;
  final int missing;
  final int closed;
  final String? observedAt;
}

final class AtlasJobDetail {
  AtlasJobDetail({
    required this.jobKey,
    this.title,
    this.description,
    this.status,
    this.closingDate,
    this.closesAtLocal,
    this.closesTimezone,
    this.applyURL,
    this.sourceURL,
    this.deadlineInfo,
    required this.displaySections,
  });

  factory AtlasJobDetail.fromJson(Map<String, Object?> json) {
    return AtlasJobDetail(
      jobKey: _string(json['job_key']) ?? '',
      title: _string(json['title']),
      description: _string(json['description']),
      status: _string(json['status']),
      closingDate: _string(json['closes_at']),
      closesAtLocal: _string(json['closes_at_local']),
      closesTimezone: _string(json['closes_tz']),
      applyURL: _uri(json['apply_url']),
      sourceURL: _uri(json['source_url']),
      deadlineInfo: _map(json['deadline_info']) == null
          ? null
          : AtlasDeadlineInfo.fromJson(_map(json['deadline_info'])!),
      displaySections: _list(
        json['display_sections'],
      ).map(_map).nonNulls.map(AtlasDetailSection.fromJson).toList(),
    );
  }

  final String jobKey;
  final String? title;
  final String? description;
  final String? status;
  final String? closingDate;
  final String? closesAtLocal;
  final String? closesTimezone;
  final Uri? applyURL;
  final Uri? sourceURL;
  final AtlasDeadlineInfo? deadlineInfo;
  final List<AtlasDetailSection> displaySections;
}

final class AtlasDeadlineInfo {
  AtlasDeadlineInfo({
    this.storedUTC,
    this.sourceLocal,
    this.sourceTimezone,
    this.sourceText,
  });

  factory AtlasDeadlineInfo.fromJson(Map<String, Object?> json) {
    return AtlasDeadlineInfo(
      storedUTC: _string(json['stored_utc']),
      sourceLocal: _string(json['source_local']),
      sourceTimezone: _string(json['source_timezone']),
      sourceText: _string(json['source_text']),
    );
  }

  final String? storedUTC;
  final String? sourceLocal;
  final String? sourceTimezone;
  final String? sourceText;
}

final class AtlasDetailSection {
  AtlasDetailSection({
    required this.title,
    this.body,
    this.rows = const <AtlasDetailRow>[],
  });

  factory AtlasDetailSection.fromJson(Map<String, Object?> json) {
    return AtlasDetailSection(
      title: _string(json['title']) ?? '',
      body: _string(json['body']),
      rows: _list(
        json['rows'],
      ).map(_map).nonNulls.map(AtlasDetailRow.fromJson).toList(),
    );
  }

  final String title;
  final String? body;
  final List<AtlasDetailRow> rows;
}

final class AtlasDetailRow {
  AtlasDetailRow({required this.label, required this.value});

  factory AtlasDetailRow.fromJson(Map<String, Object?> json) {
    return AtlasDetailRow(
      label: _string(json['label']) ?? '',
      value: _string(json['value']) ?? '',
    );
  }

  final String label;
  final String value;
}

final class AtlasRequest {
  const AtlasRequest({
    required this.method,
    required this.path,
    this.queryParameters = const <String, String>{},
    this.jsonBody,
  });

  final String method;
  final String path;
  final Map<String, String> queryParameters;
  final Map<String, Object?>? jsonBody;
}

abstract interface class AtlasTransport {
  Future<Object?> send(AtlasRequest request);
}

final class AtlasIOTransport implements AtlasTransport {
  AtlasIOTransport({required this.baseURL, HttpClient? client})
    : _client = client ?? HttpClient();

  final Uri baseURL;
  final HttpClient _client;

  @override
  Future<Object?> send(AtlasRequest request) async {
    final uri = _endpoint(request);
    final httpRequest = await _client.openUrl(request.method, uri);
    httpRequest.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (request.jsonBody != null) {
      httpRequest.headers.set(
        HttpHeaders.contentTypeHeader,
        'application/json',
      );
      final bodyBytes = utf8.encode(jsonEncode(request.jsonBody));
      httpRequest.contentLength = bodyBytes.length;
      httpRequest.add(bodyBytes);
    }
    final response = await httpRequest.close();
    final body = await utf8.decoder.bind(response).join();
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AtlasAPIException.http(response.statusCode, body);
    }
    if (body.trim().isEmpty) {
      return null;
    }
    return jsonDecode(body);
  }

  Uri _endpoint(AtlasRequest request) {
    final basePath = baseURL.path.endsWith('/')
        ? baseURL.path.substring(0, baseURL.path.length - 1)
        : baseURL.path;
    return baseURL.replace(
      path: '$basePath/${request.path}',
      queryParameters: request.queryParameters.isEmpty
          ? null
          : request.queryParameters,
    );
  }
}

final class AtlasAPIClient {
  AtlasAPIClient({Uri? baseURL, AtlasTransport? transport})
    : baseURL = baseURL ?? defaultBaseURL(),
      _transport =
          transport ?? AtlasIOTransport(baseURL: baseURL ?? defaultBaseURL());

  static const baseURLDefaultsKey = 'atlas.api.baseURL';

  final Uri baseURL;
  final AtlasTransport _transport;

  static Uri defaultBaseURL() => Uri.parse('http://127.0.0.1:8765');

  static Uri? normalizedBaseURL(String rawValue) {
    final value = rawValue.trim();
    if (value.isEmpty) {
      return null;
    }
    final prefixed = value.contains('://') ? value : 'http://$value';
    final uri = Uri.tryParse(prefixed);
    if (uri == null ||
        (uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty) {
      return null;
    }
    return Uri(
      scheme: uri.scheme.toLowerCase(),
      userInfo: uri.userInfo,
      host: uri.host,
      port: uri.hasPort ? uri.port : 0,
    );
  }

  Future<AtlasHealthSummary> health() async {
    final json = await _requestMap(
      const AtlasRequest(method: 'GET', path: 'api/health'),
    );
    return AtlasHealthSummary.fromJson(json);
  }

  Future<AtlasSearchResponse> search(AtlasSearchRequest request) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'POST',
        path: 'api/search',
        jsonBody: request.toJson(),
      ),
    );
    return AtlasSearchResponse.fromJson(json);
  }

  Future<AtlasJobDetail> jobDetail(String jobKey) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'GET',
        path: 'api/job-detail',
        queryParameters: {'job_key': jobKey},
      ),
    );
    return AtlasJobDetail.fromJson(json);
  }

  Future<List<AtlasSavedSearch>> savedSearches() async {
    final json = await _transport.send(
      const AtlasRequest(method: 'GET', path: 'api/saved-searches'),
    );
    return _list(
      json,
    ).map(_map).nonNulls.map(AtlasSavedSearch.fromJson).toList();
  }

  Future<AtlasSavedSearch> saveSearch({
    required String name,
    required AtlasSearchRequest request,
    required String summary,
  }) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'POST',
        path: 'api/saved-searches',
        jsonBody: {
          'name': name,
          'request': request.toJson(),
          'summary': summary,
        },
      ),
    );
    return AtlasSavedSearch.fromJson(json);
  }

  Future<bool> deleteSavedSearch(String name) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'DELETE',
        path: 'api/saved-searches/${Uri.encodeComponent(name)}',
      ),
    );
    return _bool(json['deleted']) ?? false;
  }

  Future<AtlasApplicationRecord> saveJob(String jobKey) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'POST',
        path: 'api/tracker/jobs/${Uri.encodeComponent(jobKey)}',
      ),
    );
    return AtlasApplicationRecord.fromJson(json);
  }

  Future<List<AtlasApplicationRecord>> trackerRecords() async {
    final json = await _transport.send(
      const AtlasRequest(method: 'GET', path: 'api/tracker'),
    );
    return _list(
      json,
    ).map(_map).nonNulls.map(AtlasApplicationRecord.fromJson).toList();
  }

  Future<bool> deleteTrackerRecord(String id) async {
    final json = await _requestMap(
      AtlasRequest(
        method: 'DELETE',
        path: 'api/tracker/${Uri.encodeComponent(id)}',
      ),
    );
    return _bool(json['deleted']) ?? false;
  }

  Future<List<AtlasSourceRun>> updates() async {
    final json = await _requestMap(
      const AtlasRequest(method: 'GET', path: 'api/updates'),
    );
    return _list(
      json['recent_source_runs'],
    ).map(_map).nonNulls.map(AtlasSourceRun.fromJson).toList();
  }

  Future<List<AtlasSourceSummary>> sources() async {
    final json = await _requestMap(
      const AtlasRequest(method: 'GET', path: 'api/sources'),
    );
    return _list(
      json['sources'],
    ).map(_map).nonNulls.map(AtlasSourceSummary.fromJson).toList();
  }

  Future<Map<String, Object?>> _requestMap(AtlasRequest request) async {
    final json = await _transport.send(request);
    final map = _map(json);
    if (map == null) {
      throw const AtlasAPIException.invalidResponse();
    }
    return map;
  }
}

final class AtlasAPIException implements Exception {
  const AtlasAPIException.invalidResponse()
    : message = 'The server returned an invalid response.';

  const AtlasAPIException.http(int statusCode, String body)
    : message = body == ''
          ? 'The server returned HTTP $statusCode.'
          : 'The server returned HTTP $statusCode: $body';

  final String message;

  @override
  String toString() => message;
}

String displayAtlasFilterValue(String value) {
  final trimmed = value.trim();
  if (trimmed.isEmpty) {
    return 'Unknown';
  }
  final upper = trimmed.toUpperCase();
  if (upper.length <= 5 && RegExp(r'^[A-Z0-9]+$').hasMatch(upper)) {
    return upper;
  }
  return trimmed
      .replaceAll('_', ' ')
      .split(RegExp(r'\s+'))
      .where((word) => word.isNotEmpty)
      .map((word) {
        if (word.length <= 4 && RegExp(r'^[A-Za-z]+$').hasMatch(word)) {
          return word.toUpperCase();
        }
        return word[0].toUpperCase() + word.substring(1);
      })
      .join(' ');
}

Map<String, Object?>? _map(Object? value) {
  if (value is Map<String, Object?>) {
    return value;
  }
  if (value is Map) {
    return value.map((key, value) => MapEntry(key.toString(), value));
  }
  return null;
}

List<Object?> _list(Object? value) => value is List ? value : const <Object?>[];

String? _string(Object? value) {
  if (value == null) {
    return null;
  }
  final string = value.toString().trim();
  return string.isEmpty ? null : string;
}

List<String> _stringList(
  Object? value, {
  List<String> fallback = const <String>[],
}) {
  if (value is! List) {
    return fallback;
  }
  return value.map(_string).nonNulls.toList();
}

int? _int(Object? value) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '');
}

double? _double(Object? value) {
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value?.toString() ?? '');
}

bool? _bool(Object? value) {
  if (value is bool) {
    return value;
  }
  if (value is num) {
    return value != 0;
  }
  final string = value?.toString().toLowerCase();
  if (string == 'true') {
    return true;
  }
  if (string == 'false') {
    return false;
  }
  return null;
}

DateTime? _date(Object? value) {
  final string = _string(value);
  if (string == null) {
    return null;
  }
  return DateTime.tryParse(string);
}

Uri? _uri(Object? value) {
  final string = _string(value);
  if (string == null) {
    return null;
  }
  return Uri.tryParse(string);
}

double? _normalizedScore(double? value) {
  if (value == null) {
    return null;
  }
  final normalized = value > 1 ? value / 100 : value;
  return normalized.clamp(0, 1).toDouble();
}

String _displayLabel(String? value, {required String fallback}) {
  final clean = value?.trim();
  if (clean == null || clean.isEmpty) {
    return fallback;
  }
  return displayAtlasFilterValue(clean);
}

String _displayGradeOptional(String? value) {
  final clean = value?.trim();
  if (clean == null || clean.isEmpty) {
    return 'Grade unknown';
  }
  return _displayGrade(clean);
}

String _displayGrade(String value) {
  final compact = value.replaceAll('-', '').toUpperCase();
  final digitIndex = compact.indexOf(RegExp('[0-9]'));
  if (compact.length >= 2 && digitIndex > 0) {
    return '${compact.substring(0, digitIndex)}-${compact.substring(digitIndex)}';
  }
  return compact;
}

String _matchSummary({
  required Map<String, Object?>? location,
  required Map<String, Object?>? grade,
  required Map<String, Object?>? scope,
  required String dutyStation,
}) {
  final parts = <String>[];
  if (location != null) {
    final source = _displayLabel(
      _string(location['source_field']),
      fallback: 'location evidence',
    );
    parts.add(
      'Location matched $dutyStation from $source at ${_percent(_double(location['confidence']))}.',
    );
  }
  if (grade != null) {
    final source = _displayLabel(
      _string(grade['source_field']),
      fallback: 'grade evidence',
    );
    parts.add(
      'Grade ${_displayGradeOptional(_string(grade['matched_grade']))} matched from $source at ${_percent(_double(grade['confidence']))}.',
    );
  }
  final matched = _string(scope?['matched']);
  if (scope != null && matched != null) {
    final reason = _displayLabel(
      _string(scope['reason']),
      fallback: 'classification',
    );
    parts.add(
      'Scope matched ${_displayLabel(matched, fallback: matched)} via $reason.',
    );
  }
  return parts.isEmpty
      ? 'Matched the current search filters.'
      : parts.join(' ');
}

String _descriptionFallback({
  required String title,
  required String organization,
  required String dutyStation,
  required String contract,
  required String modality,
}) {
  return '$title at $organization. Server detail loading will provide the full job description; this search row keeps the $contract, $modality, and $dutyStation evidence available for triage.';
}

String _percent(double? value) {
  if (value == null) {
    return 'unknown confidence';
  }
  return '${(value * 100).round()} percent confidence';
}

String _selectionSummary({
  required String prefix,
  required Set<String> values,
}) {
  final sorted = _sorted(values);
  if (sorted.isEmpty) {
    return prefix;
  }
  final firstDisplay = displayAtlasFilterValue(sorted.first);
  if (sorted.length == 1) {
    return '$prefix: $firstDisplay';
  }
  return '$prefix: $firstDisplay +${sorted.length - 1}';
}

String _gradeSortKey(String value) {
  final compact = value.replaceAll('-', '').toUpperCase();
  final digitIndex = compact.indexOf(RegExp('[0-9]'));
  if (digitIndex < 0) {
    return compact;
  }
  final letters = compact.substring(0, digitIndex);
  final numbers = compact.substring(digitIndex);
  final padded = (int.tryParse(numbers) ?? 0).toString().padLeft(3, '0');
  return '$letters$padded';
}

List<String> _backendSeniorityGroups(AtlasSearchFilters filters) {
  return _sorted(
    filters.seniorityGroups.where((value) => value != 'generic_volunteer'),
  );
}

List<String> _backendVolunteerKinds(AtlasSearchFilters filters) {
  final kinds = filters.volunteerKinds.toSet();
  if (filters.seniorityGroups.contains('volunteer')) {
    kinds.add(AtlasVolunteerKind.unVolunteer.value);
  }
  if (filters.seniorityGroups.contains('generic_volunteer')) {
    kinds.add(AtlasVolunteerKind.volunteer.value);
  }
  return _sorted(kinds);
}

List<String> _sorted(Iterable<String> values) {
  final sorted = values.toList()..sort();
  return sorted;
}

bool _setEquals(Set<String> left, Set<String> right) {
  return left.length == right.length && left.containsAll(right);
}

bool _listEquals(List<String> left, List<String> right) {
  if (left.length != right.length) {
    return false;
  }
  for (var index = 0; index < left.length; index += 1) {
    if (left[index] != right[index]) {
      return false;
    }
  }
  return true;
}

String _dateOnly(DateTime value) {
  final date = value.toUtc();
  final month = date.month.toString().padLeft(2, '0');
  final day = date.day.toString().padLeft(2, '0');
  return '${date.year}-$month-$day';
}

String _monthAbbreviation(int month) {
  const months = <String>[
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return months[month - 1];
}

Map<String, Map<String, int>> _facetMap(Object? value) {
  final root = _map(value);
  if (root == null) {
    return const <String, Map<String, int>>{};
  }
  return root.map((key, value) {
    final nested = _map(value) ?? const <String, Object?>{};
    return MapEntry(
      key,
      nested.map((nestedKey, nestedValue) {
        return MapEntry(nestedKey, _int(nestedValue) ?? 0);
      }),
    );
  });
}

Map<String, Map<String, String>> _facetLabelMap(Object? value) {
  final root = _map(value);
  if (root == null) {
    return const <String, Map<String, String>>{};
  }
  return root.map((key, value) {
    final nested = _map(value) ?? const <String, Object?>{};
    return MapEntry(
      key,
      nested.map((nestedKey, nestedValue) {
        return MapEntry(nestedKey, nestedValue.toString());
      }),
    );
  });
}
