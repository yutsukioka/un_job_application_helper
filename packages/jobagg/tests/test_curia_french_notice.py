from pathlib import Path
from datetime import datetime,timezone
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter,_eu_full_notice_signals
from jobagg.http import HttpResponse
from jobagg.pipelines.sync_source import load_sources

FIX=Path(__file__).parent/'fixtures/eu_careers'
SUMMARY='https://eu-careers.europa.eu/en/job-opportunities/2nd-assistant/cj-ap-24-26'
OFFICIAL='https://curia.europa.eu/site/jcms/p1_1000086945/en/a-legal-assistant-position-2nd-assistant'


def test_current_english_route_with_full_french_notice_is_accepted_and_preserved():
    bodies={SUMMARY:(FIX/'curia_24_26_summary_20260913.html').read_text(),OFFICIAL:(FIX/'curia_24_26_official_20260913.html').read_text()}
    class HTTP:
        def get(self,url,**kwargs):return HttpResponse(url,200,{'Content-Type':'text/html'},bodies[url],bodies[url].encode())
    source=next(s for s in load_sources(Path(__file__).parents[1]/'config/organizations.yaml') if s.id=='eu_careers_static')
    job=StaticHTMLAdapter(AdapterContext(source,HTTP())).fetch_detail_for_listing_item({'external_id':'cj-ap-24-26','title':'2nd assistant','href':SUMMARY,'parser':'eu_careers_open_vacancies'})
    assert job.external_id=='cj-ap-24-26'
    assert 'plusieurs tâches juridiques' in job.description
    assert 'formation juridique' in job.description
    assert '30 septembre 2026 à 23h45' in job.description
    assert job.closes_at==datetime(2026,9,30,21,45,tzinfo=timezone.utc)
    assert job.closes_at_local=='30/09/2026 - 23:45 (Brussels time)'
    assert job.closes_tz=='Europe/Brussels'
    assert job.raw['required_attachment_urls']==[]


@pytest.mark.parametrize('body',[
    'A legal assistant position. Luxembourg AST3. Apply now. '*80,
    'plusieurs tâches juridiques '+ 'The essentials. '*100,
    'les fonctions à exercer exigent '+ 'The essentials. '*100,
])
def test_french_signal_extension_does_not_accept_summary_or_missing_section(body):
    assert not _eu_full_notice_signals(body)
