from types import SimpleNamespace
from datetime import UTC

import pytest

from jobagg.adapters.ipu_detail import parse_ipu_public_page, fetch_ipu_bilingual_detail
from jobagg.models import OrganizationSource

EN = 'https://www.ipu.org/work-with-ipu/vacancies/2026-08/public-role'
FR = 'https://www.ipu.org/fr/travailler-avec-nous/postes-vacants/2026-08/role-public'


def page(locale='en', node='node/1'):
    current = EN if locale == 'en' else FR
    return f'''<link rel="canonical" href="{current}">
    <link rel="alternate" hreflang="en" href="{EN}"><link rel="alternate" hreflang="fr" href="{FR}">
    <a href="{EN}" hreflang="en" data-drupal-link-system-path="{node}">English</a>
    <a href="{FR}" hreflang="fr" data-drupal-link-system-path="{node}">Français</a>
    <div class="vacancy__type">Consultancy</div><div class="vacancy__node-title">Public role {locale}</div>
    <div class="vacancy__body"><p>Full job description {locale}.</p></div>
    <div class="vacancy__field-how-to-apply">The deadline for applications is 18 September 2026 at 12.00 CEST.
    Send to <span data-cfemail="63090c01230a13164d0c1104">[email protected]</span>.</div>
    <div class="vacancy__field-deadline"><time datetime="2026-09-18T12:00:00Z">18 September 2026</time></div>'''


def test_application_section_and_decoded_public_contact_are_preserved():
    parsed = parse_ipu_public_page(page(), EN, 'en')
    assert 'Full job description en.' in parsed['full_text']
    assert 'job@ipu.org' in parsed['full_text']
    assert '12.00 CEST' in parsed['full_text']
    assert parsed['translation_mapping']['drupal_node'] == 'node/1'


def test_body_only_response_cannot_certify_full_public_job():
    body = page().replace('vacancy__field-how-to-apply', 'unrelated-field')
    with pytest.raises(ValueError, match='application/deadline'):
        parse_ipu_public_page(body, EN, 'en')


def test_bilingual_refresh_retains_english_identity_and_visible_timezone():
    source = OrganizationSource(id='ipu_static_html', name='IPU', ats_family='ipu_static_html', base_url=EN)
    adapter = SimpleNamespace(source=source, fetch_text=lambda url: {EN: page(), FR: page('fr')}[url])
    job = fetch_ipu_bilingual_detail(adapter, {'external_id': 'original-english-id'}, EN)
    assert job.external_id == 'original-english-id'
    assert 'Full job description en.' in job.description
    assert 'Full job description fr.' in job.description
    assert job.closes_at.astimezone(UTC).hour == 10
    assert job.closes_at_local == '2026-09-18T12:00:00'
    assert job.closes_tz == 'Europe/Zurich'
    assert len(job.raw['_jobagg_public_language_variants']) == 2


def test_french_other_node_rejects_translation_inference():
    source = OrganizationSource(id='ipu_static_html', name='IPU', ats_family='ipu_static_html', base_url=EN)
    adapter = SimpleNamespace(source=source, fetch_text=lambda url: {EN: page(), FR: page('fr', 'node/999')}[url])
    with pytest.raises(ValueError, match='reciprocally'):
        fetch_ipu_bilingual_detail(adapter, {}, EN)


def test_canonical_response_must_match_requested_public_page():
    with pytest.raises(ValueError, match='canonical'):
        parse_ipu_public_page(page(), FR, 'fr')
