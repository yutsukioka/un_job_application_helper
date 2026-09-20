from pathlib import Path
import ast
import hashlib
import json
import re

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.adapters.taleo_public_bindings import public_bindings
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / 'fixtures/taleo_public_bindings'


def fixture(source):
    path = next(FIXTURES.glob(source + '_*.html'))
    evidence = json.loads(path.with_suffix('.html.provenance.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence['body_sha256']
    return path.read_text(), evidence['original_url']


def adapter(source):
    _, url = fixture(source)
    return TaleoAdapter(AdapterContext(OrganizationSource(source, source, 'taleo', url), object()))


@pytest.mark.parametrize('source,identity,location,department,closing', [
    ('wipo_taleo','26247-FT_LT','CH-Geneva','PCT Operations Division, PCT Services Department, Patents and Technology Sector','2026-09-16T21:59:00+00:00'),
    ('iaea_taleo','PIP-MTCD-013','Field (i.e. outside regular IAEA duty station)','MTCD-Division of Conference and Document Services','2026-09-25T21:59:00+00:00'),
    ('who_taleo','2603964','Burkina Faso-Ouagadougou','AF/DPC Health Promotion, Disease Prevention and Control','2026-09-30T21:59:00+00:00'),
    ('adb_taleo','260700','Asian Development Bank-Mongolia Resident Mission-Mongolia-Ulaanbaatar','Private Sector Operations Department','2026-09-16T15:59:00+00:00'),
    ('fao_taleo','2601865','Bolivia, Plurinational State of-Chuquisaca','FLBOL - FAO Representation in Bolivia','2026-09-16T21:59:00+00:00'),
])
def test_actual_bound_public_fields_replace_false_or_numeric_metadata(source, identity, location, department, closing):
    body, url = fixture(source)
    job = adapter(source).parse_detail_html(body,url)
    assert (job.external_id,job.location,job.department) == (identity,location,department)
    assert job.closes_at.isoformat() == closing
    assert job.raw['_taleo_public_metadata_resolution']['kind'] == 'paired_public_dom_bindings'
    assert location in job.description and department in job.description
    for field in job.raw['_taleo_flat']['_taleo_public_bindings']['visible_fields']:
        if field.get('public_text'):
            assert field['public_text'] in job.description
    assert job.raw['_taleo_record_kind'] == 'detail'


def test_wipo_different_public_labels_in_one_row_do_not_swap_dates_or_add_hidden_sites():
    body,url = fixture('wipo_taleo')
    job = adapter('wipo_taleo').parse_detail_html(body,url)
    flat = job.raw['_taleo_flat']
    assert flat['Grade'] == 'G6'
    assert flat['Contract Duration'] == '2 years (maximum cumulative length of 5 years) *'
    assert flat['Publication Date'] == '17-Aug-2026'
    assert flat['Application Deadline'] == '16-Sep-2026, 11:59:00 PM'
    assert job.posted_at is None
    assert job.raw['_taleo_posting_time_resolution']['kind'] == 'public_calendar_date_only'
    assert any(f['semantic'] == 'reqlistitem.sites' and f['reason'] == 'no public DOM target'
               for f in flat['_taleo_public_bindings']['excluded_control_bindings'])
    assert 'WIPO Headquarters, 34, chemin des Colombettes' not in job.description


def test_iaea_iso_public_clock_and_other_locations_are_preserved():
    body,url = fixture('iaea_taleo')
    job = adapter('iaea_taleo').parse_detail_html(body,url)
    assert job.posted_at.isoformat() == '2026-09-11T06:30:48+00:00'
    assert job.raw['_taleo_flat']['OTHER_LOCATIONS'] == 'Austria'
    assert job.employment_type is None
    unknown = adapter('iaea_taleo').parse_detail_html(body,url.split('&tz=')[0])
    assert unknown.closes_at is None and unknown.closes_tz is None
    assert unknown.closes_at_local == '2026-09-25, 11:59:00 PM'


def test_who_custom_contract_label_and_public_notice_survive():
    body,url = fixture('who_taleo')
    job = adapter('who_taleo').parse_detail_html(body,url)
    assert job.employment_type == 'Temporary appointment under Staff Rule 420.4'
    assert job.raw['_taleo_flat']['CONTRACT_DURATION'] == '12 Months'
    assert 'IMPORTANT NOTICE:' in job.description
    assert job.posted_at.isoformat() == '2026-09-09T15:09:40+00:00'


def test_fao_spanish_semantic_aliases_and_all_public_custom_sections_survive():
    body,url = fixture('fao_taleo')
    job = adapter('fao_taleo').parse_detail_html(body,url)
    assert job.employment_type == 'NPP (Personal Nacional de Proyecto)'
    fields = job.raw['_taleo_flat']['_taleo_public_bindings']['visible_fields']
    custom_html = [f for f in fields if re.fullmatch(r'reqlistitem.G\d+',f['semantic']) and '%3C' in f['encoded_value']]
    assert len(custom_html) == 3
    assert all(f['public_text'] in job.description for f in custom_html if f['public_text'])


@pytest.mark.parametrize('kind',['values_short','semantics_short','duplicate_target','duplicate_schema','malicious_literal'])
def test_malformed_bindings_refuse_heuristic_fallback(kind):
    body,_ = fixture('wipo_taleo')
    values = adapter('wipo_taleo')._detail_fill_list_values(body)
    if kind == 'values_short':
        values.pop()
    elif kind == 'semantics_short':
        body=body.replace("'reqlistitem.G170205120713',",'',1)
    elif kind == 'duplicate_target':
        body=body.replace("'reqTitleLinkAction','reqContestNumberValue'","'reqTitleLinkAction','reqTitleLinkAction'",1)
    elif kind == 'duplicate_schema':
        body += body[body.index('  descRequisition: {'):body.index('  descRequisition: {')+4000]
    else:
        body=body.replace("_hles: ['ID1206'", "_hles: [__import__('os').system('should_never_execute')",1)
    with pytest.raises(ValueError):
        public_bindings(body,values)


def test_triplet_reordering_cannot_change_public_dom_order_or_field_identity():
    body,url = fixture('wipo_taleo')
    a=adapter('wipo_taleo')
    expected=a.parse_detail_html(body,url)
    values=a._detail_fill_list_values(body)
    m=re.search(r'descRequisition:\s*\{\s*_size:\s*1,\s*_hles:\s*(\[[^\]]*\]),\s*_hlid:\s*(\[[^\]]*\])',body)
    targets,semantics=map(ast.literal_eval,m.groups())
    order=list(reversed(range(len(values))))
    updated=body
    for original,array in zip(m.groups(),(targets,semantics),strict=True):
        updated=updated.replace(original,repr([array[i]for i in order]),1)
    f=re.search(r"api.fillList\('requisitionDescriptionInterface', 'descRequisition', (\[.*?\])\);",updated,re.S)
    updated=updated[:f.start(1)]+repr([values[i]for i in order])+updated[f.end(1):]
    actual=a.parse_detail_html(updated,url)
    assert actual.description == expected.description
    assert (actual.external_id,actual.location,actual.closes_at)==(expected.external_id,expected.location,expected.closes_at)


@pytest.mark.parametrize('source,expected', [('Sep 9, 2026, 5:09:40 PM','2026-09-09T17:09:40'),
    ('2026-09-11, 8:30:48 AM','2026-09-11T08:30:48'), ('2026-09-25, 11:59 PM','2026-09-25T23:59:00'),
    ('2026-09-25','2026-09-25T00:00:00'), ('31-Aug-2026','2026-08-31T00:00:00')])
def test_provider_public_calendar_formats(source,expected):
    assert TaleoAdapter._localized_taleo_date(source).isoformat() == expected
