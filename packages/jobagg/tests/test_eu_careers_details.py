import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter, _eu_next_page
from jobagg.models import OrganizationSource
from jobagg.http import HttpResponse


def page(job_id, next_page=None):
    link = f'<a aria-label="Go to next page" href="?page={next_page}">Next</a>' if next_page else ""
    return f"""<table><tr><td class="views-field-title"><a href="/en/job-opportunities/role/{job_id}">Role {job_id}</a></td></tr></table><nav class="ecl-pagination">{link}</nav>"""


class HTTP:
    def __init__(self, pages):
        self.pages = iter(pages)

    def get(self, url):
        return HttpResponse(url, 200, {}, next(self.pages))


def adapter(pages, cap=20):
    source = OrganizationSource(
        "eu_careers_static",
        "EU",
        "static_html",
        "https://eu-careers.europa.eu/en/jobs",
        extra={"parser": "eu_careers_open_vacancies", "max_pages": cap},
    )
    return StaticHTMLAdapter(AdapterContext(source, HTTP(pages)))


def test_follows_all_pages_and_preserves_listing_identity():
    a = adapter([page("a", 1), page("b", 2), page("c")])
    jobs = a.fetch_jobs()
    assert [j.external_id for j in jobs] == ["a", "b", "c"]
    assert jobs[0].raw["external_id"] == "a"
    assert a.run_diagnostics.pages_fetched == 3
    assert a.run_diagnostics.pagination_complete is True


def test_cap_is_failure_not_success():
    a = adapter([page("a", 1)], cap=1)
    assert len(a.fetch_jobs()) == 1
    assert a.run_diagnostics.pagination_complete is False
    assert "cap" in a.run_diagnostics.empty_reason


def test_repeated_id_fails_unstable_census():
    a = adapter([page("a", 1), page("a")])
    assert len(a.fetch_jobs()) == 1
    assert a.run_diagnostics.pagination_complete is False
    assert "repeated" in a.run_diagnostics.empty_reason


@pytest.mark.parametrize(
    "href", ["https://other.example/jobs?page=1", "?page=2", "?page=1&filter=x"]
)
def test_next_page_cannot_change_scope_or_skip(href):
    with pytest.raises(ValueError):
        _eu_next_page(
            f'<a aria-label="Go to next page" href="{href}">Next</a>',
            "https://eu-careers.europa.eu/en/jobs",
        )


def summary(target="https://agency.example/job/REF-2026-123"):
    return f'<main><h1>Scientific Research Officer</h1><div class="field--name-field-epso-link"><a href="{target}">Link</a></div></main>'


def notice():
    return (
        "<main><h1>Scientific Research Officer</h1><h2>Duties</h2>"
        + "<p>Research duties and delivery across projects.</p>" * 30
        + "<h2>Eligibility requirements</h2><p>University degree and seven years of experience.</p><div><span>Nested qualifications.</span> Crucial final tail.</div></main>"
    )


def detail_item():
    return {
        "parser": "eu_careers_open_vacancies",
        "href": "https://eu-careers.europa.eu/en/jobs/REF-2026-123",
        "external_id": "REF-2026-123",
        "title": "Scientific Research Officer",
    }


def test_summary_is_followed_and_complete_official_nested_notice_preserved():
    a = adapter([summary(), notice()])
    j = a.fetch_detail_for_listing_item(detail_item())
    assert j.external_id == "REF-2026-123"
    assert j.apply_url == "https://agency.example/job/REF-2026-123"
    assert "Crucial final tail." in j.description
    assert "<span>Nested qualifications.</span>" in j.raw["detail_html"]


def test_observed_notice_override_requires_explicit_scope_and_preserves_original_link():
    original = "https://agency.example/job/REF-2026-123"
    target = "https://agency.example/notices/REF-2026-123/en"
    a = adapter([summary(original), notice()])
    a.source.extra.update({"official_notice_url_overrides": {original: target},
                           "official_vacancy_urls": [target]})
    job = a.fetch_detail_for_listing_item(detail_item())
    assert job.apply_url == target
    assert job.raw["summary_official_vacancy_url"] == original
    assert job.raw["observed_official_url_override"] == target

    a = adapter([summary(original)])
    a.source.extra["official_notice_url_overrides"] = {original: target}
    with pytest.raises(ValueError, match="explicitly registered"):
        a.fetch_detail_for_listing_item(detail_item())


def test_thin_official_page_cannot_be_complete():
    a = adapter([summary(), "<h1>Scientific Research Officer</h1><p>Apply now.</p>"])
    with pytest.raises(ValueError, match="full duties"):
        a.fetch_detail_for_listing_item(detail_item())


def test_wrong_official_vacancy_identity_fails():
    a = adapter([summary(), notice().replace("Scientific Research Officer", "Different Job")])
    with pytest.raises(ValueError, match="identity"):
        a.fetch_detail_for_listing_item(detail_item())


def test_directory_text_cannot_replace_unique_job_notice():
    a = adapter([summary("https://agency.example/careers/vacancies"), notice()])
    with pytest.raises(ValueError, match="unique"):
        a.fetch_detail_for_listing_item(detail_item())


def test_captured_five_page_source_defects_retain_32_known_ids_without_certifying():
    import gzip
    from pathlib import Path
    from urllib.parse import urlsplit, parse_qsl

    class CapturedHTTP:
        def get(self, url):
            page_index = dict(parse_qsl(urlsplit(url).query)).get("page", "0")
            fixture = (
                Path(__file__).parent
                / "fixtures/eu_careers"
                / f"page_{page_index}_20260910.html.gz"
            )
            return HttpResponse(url, 200, {}, gzip.decompress(fixture.read_bytes()).decode())

    source = OrganizationSource(
        "eu_careers_static",
        "EU",
        "static_html",
        "https://eu-careers.europa.eu/en/job-opportunities/open-vacancies/cast",
        extra={"parser": "eu_careers_open_vacancies", "max_pages": 20},
    )
    a = StaticHTMLAdapter(AdapterContext(source, CapturedHTTP()))
    jobs = a.fetch_jobs()
    assert len(jobs) == len({j.external_id for j in jobs}) == 32
    assert a.run_diagnostics.pages_fetched == 5
    assert a.run_diagnostics.pagination_complete is False
    assert "requested_page_2_returned_1" in a.run_diagnostics.empty_reason
    assert "repeated_ids" in a.run_diagnostics.empty_reason


def test_official_host_cooldown_prevents_request():
    a = adapter([])
    a.source.extra["official_host_cooldowns"] = {"agency.example": "2999-01-01T00:00:00+00:00"}
    with pytest.raises(RuntimeError, match="cooldown"):
        a._eu_get_official("https://agency.example/job/1")


def test_failed_official_host_is_not_retried_for_other_jobs_in_same_pass():
    class FailedHTTP:
        calls = 0

        def get(self, url):
            self.calls += 1
            raise RuntimeError("HTTP403 Forbidden")

    a = adapter([])
    a.context.http = FailedHTTP()
    with pytest.raises(RuntimeError, match="Forbidden"):
        a._eu_get_official("https://agency.example/job/1")
    with pytest.raises(RuntimeError, match="stopped for this pass"):
        a._eu_get_official("https://agency.example/job/2")
    assert a.context.http.calls == 1


def test_eulisa_current_offer_pdf_is_bound_by_exact_reference_excluding_similar_offers(monkeypatch):
    import json
    from pathlib import Path
    from jobagg.adapters import static_html

    public = json.loads((Path(__file__).parent / "fixtures/eu_primary_metadata/current_eight_20260913.json").read_text())["jobs"][0]

    url = "https://erecruitment.eulisa.europa.eu/en/our-jobs/head-of-budget-and-finance-unit-226"
    pdf = "https://erecruitment.eulisa.europa.eu/assets/offers/228_RR_EU_Vacancy_Notice.pdf?571707"
    offer = {
        "reference": "eu-LISA/26/TA/AD10/15.1",
        "uri": url,
        "content": f'<a href="{pdf}">Download PDF</a>',
        "similarOffers": [
            {
                "reference": "private/internal",
                "content": '<a href="https://erecruitment.eulisa.europa.eu/wrong.pdf">Other job</a>',
            }
        ],
    }
    wrapper = (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"offer": offer}}})
        + "</script>"
    )
    a = adapter([summary(url), wrapper, "%PDF"])
    item = {
        **detail_item(),
        "href": public["source_url"],
        "external_id": "eu-lisa-26-ta-ad10-151",
        "title": "Head of Finance and Budget Unit",
    }
    monkeypatch.setattr(
        static_html,
        "_extract_pdf_text",
        lambda _: public["raw"]["official_notice_text"],
    )
    j = a.fetch_detail_for_listing_item(item)
    assert j.apply_url == pdf
    assert j.raw["required_attachment_urls"] == [pdf]
    assert j.raw["official_offer_reference"] == offer["reference"]
    assert "wrong.pdf" not in j.raw["detail_html"]
    assert j.title == "Head of Budget and Finance Unit"
    assert j.employment_type == "Temporary Staff"
    assert j.posted_at is None


def test_eulisa_wrong_current_reference_cannot_match_similar_offer():
    import json
    from jobagg.adapters.static_html import _eu_lisa_offer

    url = "https://erecruitment.eulisa.europa.eu/en/our-jobs/example-1"
    offer = {
        "reference": "WRONG",
        "uri": url,
        "similarOffers": [{"reference": "eu-LISA/26/TA/AD10/15.1"}],
    }
    html = (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"offer": offer}}})
        + "</script>"
    )
    with pytest.raises(ValueError, match="identity"):
        _eu_lisa_offer(html, url, "eu-lisa-26-ta-ad10-151")


def test_http_200_challenge_stops_official_host_for_this_pass():
    a = adapter(["<html><title>Just a moment...</title><body>Challenge</body></html>"])
    with pytest.raises(RuntimeError, match="access challenge"):
        a._eu_get_official("https://agency.example/notice.pdf")
    with pytest.raises(RuntimeError, match="stopped for this pass"):
        a._eu_get_official("https://agency.example/notice-two.pdf")


def test_euosha_follows_authoritative_english_pdf_not_machine_translation(monkeypatch):
    from jobagg.adapters import static_html

    wrapper_url = "https://euosha.gestmax.eu/837/1/procurement-officer-ast3"
    body = '<p>The EN original version therefore prevails for all purposes.</p><a href="/english.pdf">en</a><a href="/french.pdf">fr</a>'
    a = adapter([summary(wrapper_url), body, "%PDF"])
    monkeypatch.setattr(
        static_html, "_extract_pdf_text", lambda _: static_html._clean_html(notice())
    )
    j = a.fetch_detail_for_listing_item(detail_item())
    assert j.raw["required_attachment_urls"] == ["https://euosha.gestmax.eu/english.pdf"]


def test_etf_pdf_form_selects_english_and_does_not_capture_application_fields():
    from jobagg.adapters.static_html import _ETFViewPDFForm

    parser = _ETFViewPDFForm()
    parser.feed("""<form id="application"><input type="hidden" name="form_id" value="apply"></form>
    <form id="etf-ui-attachment-dropdown-select-form1" action="/job"><input type="hidden" name="form_id" value="etf_ui_attachment_dropdown_select_form1"><input type="hidden" name="form_build_id" value="fresh-public-token"><select><option value="2">French</option><option value="1">English</option></select></form>""")
    assert parser.fields == {
        "form_id": "etf_ui_attachment_dropdown_select_form1",
        "form_build_id": "fresh-public-token",
    }
    assert parser.english == "1" and parser.action == "/job"


def test_curia_contribution_heading_is_a_positive_duties_signal():
    from jobagg.adapters.static_html import _eu_full_notice_signals

    text = (
        "What will your contribution be? "
        + ("Implement security controls. " * 50)
        + "Basic qualifications: University degree."
    )
    assert _eu_full_notice_signals(text)


def test_retained_official_fragment_and_summary_keep_distinct_original_link_bases():
    from urllib.parse import urljoin
    from jobagg.adapters.static_html import _EUAnchors

    official = "https://curia.europa.eu/site/jcms/p1_1000084651/en/cybersecurity-expert"
    relative_pdf = "jcms/p1_1000084650/en/cj-ap-cybersec-perm"
    official_html = ('<head><base href="https://curia.europa.eu/site/"></head>'
                     + notice().replace("</main>", f'<a href="{relative_pdf}">Download a pdf version</a></main>'))
    summary_html = summary(official).replace(
        "</main>", '<a href="/en/documents/framework/13068">Competency framework</a></main>')
    job = adapter([summary_html, official_html]).fetch_detail_for_listing_item(detail_item())
    detail_parser, summary_parser = _EUAnchors(), _EUAnchors()
    detail_parser.feed(job.raw["detail_html"])
    summary_parser.feed(job.raw["summary_html"])
    assert urljoin(detail_parser.base_href, relative_pdf) == (
        "https://curia.europa.eu/site/jcms/p1_1000084650/en/cj-ap-cybersec-perm")
    assert urljoin(summary_parser.base_href, "/en/documents/framework/13068") == (
        "https://eu-careers.europa.eu/en/documents/framework/13068")
    assert "Crucial final tail." in job.description
    assert "<base" not in job.description


def test_summary_metadata_preserves_brussels_deadline_and_multiple_domains():
    from jobagg.adapters.static_html import _eu_summary_metadata
    document = '''<main><h1>Officer</h1><div>Domain(s)</div><div>Finance</div><div>Administration</div>
    <div>Reference number</div><div>CA/26/01</div><div>Deadline</div><div>15/09/2026 - 11:59 (Brussels time)</div>
    <div>Location(s):&nbsp;</div><div>Barcelona (Spain)</div><div>Grade:&nbsp;</div><div>AD 6</div>
    <div>Institution/Agency</div><a href="/agency">Agency</a><div>Type of contract</div><a href="/type">Temporary staff</a>
    <div>Link to vacancy</div><a href="/job">Notice</a><p>Footer prose</p></main>'''
    metadata = _eu_summary_metadata(document)
    assert metadata['domain'] == 'Finance; Administration'
    assert metadata['closes_at'] == '2026-09-15T11:59:00+02:00'
    assert metadata['location'] == 'Barcelona (Spain)'
    assert metadata['institution'] == 'Agency'
    assert metadata['contract_type'] == 'Temporary staff'
    assert 'Footer prose' not in str(metadata)


def test_navigation_image_article_does_not_hide_full_main_vacancy():
    official = '<nav><article><img src="portrait.png"></article></nav>' + notice()
    job = adapter([summary(), official]).fetch_detail_for_listing_item(detail_item())
    assert 'Crucial final tail.' in job.description


def test_comments_with_nested_markup_do_not_leave_delimiters_or_duplicate_navigation():
    official = notice().replace('</main>', '<!-- Apply > for <a href="/apply">Apply for Job</a> --> Tail.</main>')
    job = adapter([summary(),official]).fetch_detail_for_listing_item(detail_item())
    assert '-->' not in job.description and 'Apply for Job' not in job.description
    assert 'Tail.' in job.description


def test_bilingual_title_requires_both_components_and_french_notice_sections():
    from jobagg.adapters.static_html import _eu_identity_matches, _eu_full_notice_signals
    title = 'Social Worker (Assistant/e Social/e)'
    body = ('Social Worker in the Working Conditions Unit. Dans votre rôle d’assistant(e) social(e). '
            + 'Quelle sera votre contribution ? Conseiller le personnel. ' * 30
            + 'Conditions de base : diplôme requis. Conditions souhaitées : expérience pertinente.')
    assert _eu_identity_matches(body, title, 'cj-ap-23-26')
    assert _eu_full_notice_signals(body)
    assert not _eu_identity_matches('Social Worker in another unit', title, 'cj-ap-23-26')


@pytest.mark.parametrize('wrapper,label,pdf', [
    ('https://eurojust.tal.net/candidate/opp/30', 'Vacancy Notice - Legal Officer.pdf',
     'https://eurojust.tal.net/candidate/download_file_opp/30/180475/1/0/public-token'),
    ('https://www.cor.europa.eu/en/about/work-us/jobs/assistant', 'Download Assistant in Records',
     'https://www.cor.europa.eu/sites/default/files/2026-09/notice.pdf'),
])
def test_required_notice_pdf_is_followed_from_observed_official_anchor(monkeypatch, wrapper, label, pdf):
    from jobagg.adapters import static_html
    page_html = '<h1>Scientific Research Officer</h1><a href="'+pdf+'">'+label+'</a>'
    a = adapter([summary(wrapper),page_html,'%PDF'])
    monkeypatch.setattr(static_html,'_extract_pdf_text',lambda _:static_html._clean_html(notice()))
    job = a.fetch_detail_for_listing_item(detail_item())
    assert job.apply_url == pdf
    assert job.raw['required_attachment_urls'] == [pdf]
    assert 'Crucial final tail.' in job.description


def test_euaa_public_form_selects_exact_notice_not_other_vacancy_or_apply_action():
    from jobagg.adapters.static_html import _EUAAVacancyForm
    prefix='ctl00$cphMain$RPVacancyCategories$ctl00$RPVacancies$'
    controls=[prefix+f'ctl0{i}$rptVacancyNoticeTranslations$ctl01$ctl00' for i in range(2)]
    page='<form id="aspnetForm" method="post" action="./"><input type="hidden" name="__VIEWSTATE" value="fresh"><input type="hidden" name="__EVENTVALIDATION" value="fresh">'
    for code,control in zip(['EUAA/2026/TA/018','EUAA/2026/TA/014'],controls):
        page+=f'''<div class="easo-panel vacancy-list"><div>{code}</div><div><a href="javascript:__doPostBack('{control}','')">EN</a><a href="javascript:__doPostBack('{prefix}ctl00$lkApplyTo','')">Apply for this Vacancy</a></div></div>'''
    page+='</form>'
    form=_EUAAVacancyForm()
    form.feed(page)
    payload=form.notice_payload('euaa-2026-ta-014')
    assert payload['__EVENTTARGET']==controls[1] and 'lkApplyTo' not in payload['__EVENTTARGET']
    assert payload['__VIEWSTATE']=='fresh' and payload['__EVENTARGUMENT']==''
    with pytest.raises(ValueError,match='exact vacancy panel'):
        form.notice_payload('euaa-2026-ca-007')
    bad=_EUAAVacancyForm()
    bad.feed(page.replace('>EN<','>Apply for this Vacancy<'))
    with pytest.raises(ValueError,match='EN notice'):
        bad.notice_payload('euaa-2026-ta-014')


def test_curia_language_variant_keeps_same_document_and_original_french_prose():
    french='https://curia.europa.eu/site/jcms/p1_123/fr/officier'
    french_body='<base href="https://curia.europa.eu/site/"><main><h1>Officier scientifique</h1><p>Texte original français.</p></main><a href="jcms/p1_123/en/officer">English / EN</a>'
    job=adapter([summary(french),french_body,notice()]).fetch_detail_for_listing_item(detail_item())
    assert job.apply_url=='https://curia.europa.eu/site/jcms/p1_123/en/officer'
    assert job.raw['original_language_notice']['document_id']=='p1_123'
    assert 'Texte original français.' in job.description
    with pytest.raises(ValueError,match='changes document identity'):
        adapter([summary(french),french_body.replace('/p1_123/en/','/p1_WRONG/en/')]).fetch_detail_for_listing_item(detail_item())
