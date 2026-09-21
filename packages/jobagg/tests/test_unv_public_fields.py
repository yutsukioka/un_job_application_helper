import copy
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.unv import UNVAdapter
from jobagg.adapters.unv_public import explicit_deadline_conflicts, public_deadline, render_public
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / 'fixtures/unv'


def fixture(online=False, observed_category_filter=False):
    identity = '1784888021272400' if online else '1784888021272041'
    item = json.loads((FIXTURES / f'public_projection_{identity}_20260913.json').read_text())
    if observed_category_filter:
        # A controlled section-filter state; the original fixture deliberately
        # keeps the still-uncaptured live setting asNone rather than invent it.
        item['_unv_public_render_context']['category_configuration_enabled'] = False
    return item


def fields(item):
    body, proof = render_public(item)
    assert proof['complete'], proof['missing']
    return body, {(r['section'], r['label']): r['value'] for r in proof['fields']}


def test_actual_zambia_public_values_include_missing_fields_and_associations():
    item = fixture(observed_category_filter=True)
    body, values = fields(item)
    expected = {(1000, 'Duty stations'): 'Lusaka', (1000, 'Duration'): '12 months\nLong-term benefits and allowances',
                (3000, 'Age'): '18 - 80', (3000, 'Required experience'): '1 month',
                (4000, 'Relevant experience'): 'No experience required',
                (4000, 'Languages'): 'English, Level: Fluent, Required',
                (4000, 'Required education level'): "Bachelor's degree in Public Finance, Economics, Social Sciences, Business Administration or any related field.",
                (4000, 'Driving license'): '-'}
    assert expected.items() <= values.items()
    for key in ('organizationMission', 'context', 'taskDescription', 'requiredSkillExperience',
                'competency', 'additionalEligibilityCriteria', 'livingConditions'):
        assert item[key] in body
    assert '16 August 2026' in body
    assert values[5000, 'Reasonable accommodation'] == item['_unv_public_render_context']['translations']['doa_detail.Unicef_first_para']
    assert values[5000, 'Note on Covid-19 vaccination requirements'] == item['_unv_public_render_context']['translations']['doa_detail.Unicef_second_para']


def test_context_still_unknown_cannot_claim_full_public_text():
    _, proof = render_public(fixture())
    assert proof['complete'] is False
    assert any('configuration' in issue for issue in proof['missing'])


def test_actual_online_uses_online_fields_and_excludes_onsite_only_raw_values():
    item = fixture(online=True, observed_category_filter=True)
    item.update(requiredEducation={'label': 'Hidden degree'}, specializationArea='Hidden specialization',
                competency='Hidden onsite competency', livingConditions='Hidden onsite living conditions')
    body, values = fields(item)
    assert values[1000, 'Modality'] == 'Online'
    assert (1000, 'For how many hours per week will the volunteer be required?') in values
    assert values[2000, 'Task type'] == item['taskType']['label']
    assert (3000, 'Age') not in values and (4000, 'Relevant experience') not in values
    assert all(value not in body for value in ('Hidden degree', 'Hidden specialization', 'Hidden onsite competency', 'Hidden onsite living conditions'))
    assert values[4000, 'Required experience'] == item['requiredSkillExperience']


def test_online_contractual_warning_is_complete_and_required_outside_section_filters():
    item = fixture(online=True, observed_category_filter=True)
    warning = item['_unv_public_render_context']['translations']['doa_detail.headers.online_warning']
    body, _ = fields(item)
    assert body.startswith(warning + '\n\n')
    assert 'Online Volunteers are not UN Volunteers' in body
    assert 'certificate of appreciation' in body
    onsite = fixture(observed_category_filter=True)
    assert warning not in fields(onsite)[0]
    item['_unv_public_render_context']['translations'].pop('doa_detail.headers.online_warning')
    _, proof = render_public(item)
    assert proof['complete'] is False
    assert any('online_warning' in value for value in proof['missing'])


@pytest.mark.parametrize('mutate', ['duty_missing', 'duty_wrong_type', 'eligibility_missing', 'eligibility_wrong_type', 'language_missing_level', 'missing_translation'])
def test_missing_or_invalid_supplement_fails_closed(mutate):
    item = fixture(observed_category_filter=True)
    if mutate == 'duty_missing':
        item.pop('_unv_duty_station_response')
    elif mutate == 'duty_wrong_type':
        item['_unv_duty_station_response'] = {'label': 'Lusaka'}
    elif mutate == 'eligibility_missing':
        item.pop('_unv_eligibility_criteria')
    elif mutate == 'eligibility_wrong_type':
        item['_unv_eligibility_criteria'] = ['invented criterion']
    elif mutate == 'language_missing_level':
        item['languages'][0]['level'] = {}
    else:
        item['_unv_public_render_context']['translations'].pop('doa_detail.Unicef_second_para')
    _, proof = render_public(item)
    assert proof['complete'] is False and proof['missing']


def test_complete_source_duty_list_preserves_duplicate_counts_and_custom_names():
    item = fixture(observed_category_filter=True)
    duties = item['_unv_duty_station_response']
    duties.append(copy.deepcopy(duties[0]))
    duties.append({'isCustomDutyStation': True, 'customDutyStation': 'Named public outpost', 'dutyStation': None})
    _, values = fields(item)
    assert values[1000, 'Duty stations'] == 'Lusaka (2), Named public outpost'


def test_public_category_section_filter_and_field_filter_are_distinct():
    item = fixture(observed_category_filter=True)
    context = item['_unv_public_render_context']
    context.update(category_configuration_enabled=True, category_sections=[1000, 2000, 3000, 4000])
    context['display_config']['fields'].remove(4004)
    body, values = fields(item)
    assert not any(section == 5000 for section, _ in values)
    assert (4000, 'Required education level') not in values
    assert 'UNICEF offers reasonable accommodation' not in body
    # Source Details does not filter its fields by displayConfig.fields.
    context['display_config']['fields'].remove(2003)
    _, values = fields(item)
    assert values[2000, 'Task description'] == item['taskDescription']


@pytest.mark.parametrize('host,onsite,generic,unicef', [('HCR', True, False, False), ('CF', True, False, True),
                                                     ('OTHER', True, True, False), ('OTHER', False, False, False),
                                                     ('CF', False, False, True)])
def test_host_and_modality_condition_public_boilerplate(host, onsite, generic, unicef):
    item = fixture(online=not onsite, observed_category_filter=True)
    item['hostEntity']['institution']['value']['code'] = host
    body, _ = fields(item)
    translations = item['_unv_public_render_context']['translations']
    assert (translations['doa_detail.vaccination_notice'] in body) is generic
    assert (translations['doa_detail.Unicef_first_para'] in body) is unicef
    assert (translations['doa_detail.Unicef_second_para'] in body) is unicef
    assert translations['doa_detail.inclusivity_statment'] in body
    assert translations['doa_detail.un_fee_notice'] in body


def test_internal_financial_and_scoring_fields_never_become_readable_public_text():
    item = fixture(observed_category_filter=True)
    item.update(overallGrade='HIDDEN_SCORE', reservedDonorUserId='HIDDEN_USER',
                erpCoa={'field': 'HIDDEN_ERP'}, proforma=[{'field': 'HIDDEN_COST'}])
    item['batch']['erpCoa'] = {'value': 'HIDDEN_BATCH_COST'}
    body, _ = fields(item)
    assert 'HIDDEN_' not in body


def test_secondary_education_and_desirable_language_driving_flags_match_ui():
    item = fixture(observed_category_filter=True)
    item.update(requiredEducation={'label': 'Secondary education', 'value': {'code': 'SEC_EDU'}},
                specializationArea='This is not displayed for SEC_EDU', drivingLicense='Class B', requiredDrivingLicense=False)
    item['languages'][0]['isRequired'] = False
    body, values = fields(item)
    assert values[4000, 'Required education level'] == 'Secondary education'
    assert 'This is not displayed for SEC_EDU' not in body
    assert values[4000, 'Languages'] == 'English, Level: Fluent, Desirable'
    assert values[4000, 'Driving license'] == 'Class B Desirable'


@pytest.mark.parametrize('source,expected', [
    ('2026-09-13T00:00:00', '2026-09-14T00:00:00+00:00'),
    ('2026-09-13', '2026-09-14T00:00:00+00:00'),
    ('2026-12-31T23:59:59.1234567Z', '2027-01-01T23:59:59.123456+00:00'),
    ('2028-02-28T00:00:00', '2028-02-29T00:00:00+00:00'),
    ('2028-02-29T00:00:00', '2028-03-01T00:00:00+00:00'),
    ('2026-03-29T00:00:00+01:00', '2026-03-29T23:00:00+00:00'),
    ('2026-10-25T00:00:00+02:00', '2026-10-25T22:00:00+00:00'),
    ('2026-09-13T23:00:00-10:00', '2026-09-15T09:00:00+00:00'),
])
def test_public_advertisement_end_follows_explicit_utc_plus_one_day(source, expected):
    result, evidence = public_deadline(source)
    assert result.isoformat() == expected
    assert evidence['original_sourcing_end_date'] == source
    assert evidence['kind'] == 'known_instant'
    assert len(evidence['public_code_sha256']) == 64


@pytest.mark.parametrize('value', [None, '', 'ongoing', '0001-01-01T00:00:00', '2026-02-30T00:00:00',
                                 '2026-09-13T25:00:00', '9999-12-31T00:00:00', True, 123])
def test_unparseable_and_sentinel_dates_stay_unknown(value):
    result, evidence = public_deadline(value)
    assert result is None and evidence['kind'] == 'unknown'


def test_conflicting_public_deadline_phrase_is_preserved_and_flagged():
    item = fixture(observed_category_filter=True)
    before = copy.deepcopy(item)
    conflicts = explicit_deadline_conflicts(item, datetime(2026, 9, 14, tzinfo=UTC))
    assert any(c['prose_calendar_date'] == '2026-08-16' for c in conflicts)
    assert item == before


def test_adapter_publishes_ui_url_and_correct_utc_while_preserving_raw_source_date():
    item = fixture(observed_category_filter=True)
    source = OrganizationSource('unv_uvp', 'UNV', 'unv', 'https://app.unv.org',
                                extra={'detail_url_template': 'https://app.unv.org/opportunities/{job_id}'})
    job = UNVAdapter(AdapterContext(source, object())).parse_jobs({'value': item})[0]
    assert job.apply_url == job.source_url == 'https://app.unv.org/opportunities/1784888021272041'
    assert job.closes_at == datetime(2026, 9, 14, tzinfo=UTC)
    assert job.closes_tz == 'UTC' and job.closes_at_local == '2026-09-14T00:00:00'
    assert job.raw['sourcingEndDate'] == '2026-09-13T00:00:00'
    assert job.raw['_unv_public_text_verification']['complete']
    assert job.raw['_unv_public_source_conflicts']


def test_public_header_funding_date_and_stop_flag_follow_public_contract():
    item = fixture(observed_category_filter=True)
    body, values = fields(item)
    assert values[0, 'Advertisement end date:'] == '14/09/2026 00:00 UTC'
    assert 'Fully funded' in body and 'Advertising stopped' not in body
    item.update(isFullyFunding=False, isSingleSource=True, isSourcingActive=False)
    body, _ = fields(item)
    assert 'Direct recruitment' in body and 'Advertising stopped' in body
    item['_unv_public_render_context']['display_config']['headers'] = [1]
    _, values = fields(item)
    assert values[0, 'Status'] == item['status']['label']
    item['status']['value']['code'] = 'DOA_RECRUITED'
    _, values = fields(item)
    assert (0, 'Advertisement end date:') not in values


@pytest.mark.parametrize('days,onsite,expected', [(15.219, True, '0 months'), (15.22, True, '1 months'),
                                                (45.66, True, '1 months'), (45.661, True, '2 months'), (3.499, False, '0 weeks'),
                                                (3.5, False, '1 weeks'), (10.5, False, '2 weeks')])
def test_public_duration_uses_source_js_rounding_boundaries(days, onsite, expected):
    item = fixture(online=not onsite, observed_category_filter=True)
    item.update(duration=days, isDurationFilled=True)
    _, values = fields(item)
    assert values[1000, 'Duration'].splitlines()[0] == expected


def support_adapter(item, *, settings='window.UVP.settings={"useCategoryConfiguration":true};'):
    from jobagg.adapters.unv_public import select_translations
    item = copy.deepcopy(item)
    context = item.pop('_unv_public_render_context')
    duties = item.pop('_unv_duty_station_response', None)
    eligibility = item.pop('_unv_eligibility_criteria', None)
    urls = {'api_url': 'https://app.unv.org/api/search', 'public_rendering': True,
            'detail_api_url_template': 'https://app.unv.org/api/doa/doa/{job_id}',
            'detail_url_template': 'https://app.unv.org/opportunities/{job_id}',
            'public_guest_profile_url': 'https://app.unv.org/api/doa/profile/me/guest',
            'public_translation_url': 'https://uvpprdpublicstorage01.blob.core.windows.net/internationalization/en/en.json?v=5964348',
            'public_settings_url': 'https://app.unv.org/config.js?v=20260818.2',
            'public_duty_station_url_template': 'https://app.unv.org/api/doa/assignment/doa/{job_id}/dutyStations',
            'public_eligibility_url_template': 'https://app.unv.org/api/backoffice/masterTables/Category/values/{category_code}/children/ElegibilityCriteria/languages/EN',
            'public_category_sections_url': 'https://app.unv.org/api/doa/taskExecutionManager/getRelatedActionsAndSectionsConfiguration'}
    # Translation helper accepts dot keys as well as nested JSON because its
    # flattening preserves existing path delimiters.
    assert select_translations(context['translations']) == context['translations']
    calls = []
    class Response:
        def __init__(self, data): self.data = data
        def json(self): return self.data
        @property
        def text(self): return self.data
    class HTTP:
        def get(self, url, **kwargs):
            calls.append(('GET', url))
            if url == urls['public_guest_profile_url']:
                return Response({'value': {'configurations': {'doa': [{'concept': 'doa_details', 'value': json.dumps({'displayConfig': context['display_config']})}]}}})
            if url == urls['public_translation_url']:
                return Response(context['translations'])
            if url == urls['public_settings_url']:
                return Response(settings)
            if '/dutyStations' in url:
                return Response({'isSuccess': True, 'value': {'dutyStationResponse': duties}})
            if '/children/ElegibilityCriteria/' in url:
                return Response({'values': eligibility})
            return Response({'value': item})
        def post_json(self, url, payload, **kwargs):
            calls.append(('POST', url, payload))
            assert url == urls['public_category_sections_url']
            return Response({'sections': [1000, 2000, 3000, 4000, 5000]})
    source = OrganizationSource('unv_test', 'UNV', 'unv', 'https://app.unv.org', extra=urls)
    return UNVAdapter(AdapterContext(source, HTTP())), calls


def test_public_support_fetches_exact_readonly_post_and_caches_shared_context():
    item = fixture()
    adapter, calls = support_adapter(item)
    for _ in range(2):
        result = adapter.fetch_detail_for_listing_item({'id': item['id']})
        assert result.raw['_unv_public_text_verification']['complete']
        assert result.raw['_unv_public_field_provenance']['category_sections']['request'] == {
            'volunteerCategoryCode': 'INT_ASO_PRE_FTM_LTR', 'entityName': 'doa,doaCandidate'}
    posts = [c for c in calls if c[0] == 'POST']
    assert len(posts) == 1
    assert sum('/dutyStations' in c[1] for c in calls) == 2
    assert sum('/profile/me/guest' in c[1] for c in calls) == 1
    assert sum('/children/ElegibilityCriteria/' in c[1] for c in calls) == 1
    assert all('execute' not in c[1].casefold() for c in calls)


def test_online_support_omits_onsite_and_category_requests():
    item = fixture(online=True)
    adapter, calls = support_adapter(item)
    result = adapter.fetch_detail_for_listing_item({'id': item['id']})
    assert result.raw['_unv_public_text_verification']['complete']
    assert not any(c[0] == 'POST' or '/dutyStations' in c[1] or '/children/' in c[1] for c in calls)


@pytest.mark.parametrize('settings', ['window.UVP.settings={};', 'useCategoryConfiguration:true; useCategoryConfiguration:false;'])
def test_missing_ambiguous_public_settings_refuses_false_certificate(settings):
    item = fixture()
    adapter, _ = support_adapter(item, settings=settings)
    with pytest.raises(ValueError, match='configuration setting'):
        adapter.fetch_detail_for_listing_item({'id': item['id']})


def test_wrong_assignment_identity_stops_before_any_support_requests():
    adapter, calls = support_adapter(fixture())
    with pytest.raises(ValueError, match='identity'):
        adapter.fetch_detail_for_listing_item({'id': 111})
    assert len(calls) == 1
