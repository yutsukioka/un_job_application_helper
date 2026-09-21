"""Historical public row/range observations exercise the bounded DOM contract.

HTML containers below are synthetic; the 81 IDs/titles/URLs and 18 page ranges
are preserved actual Sept13 observations, never claimed current source HTML.
"""

import gzip
import hashlib
import html
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.idb_inventory import board_url, parse_board
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.pipelines.inventory_checks import verify_listing

DATA = json.loads(
    (Path(__file__).parent / "fixtures/idb/fullboard_row_observations_20260913.json").read_text()
)


def source(cap=20):
    return OrganizationSource(
        "idb_successfactors",
        "IDB",
        "successfactors_rmk",
        "https://jobs.iadb.org",
        extra={
            "rss_url": "https://jobs.iadb.org/services/rss/category/?catid=9638000",
            "public_widget_root_url": "https://performancemanager8.successfactors.com/verp/vmod_3",
            "public_all_jobs_url": "https://jobs.iadb.org/go/All-Jobs/9638000/",
            "max_pages": cap,
        },
    )


def documents():
    org = source()
    result = {org.extra["rss_url"]: "<rss><channel></channel></rss>"}
    for walk in DATA["walks"]:
        sort = "date" if walk["sort"] == "Most Recent" else "relevance"
        for page in walk["pages"]:
            links = "".join(
                '<a href="'
                + html.escape(DATA["jobs"][key]["detail_url"], quote=True)
                + '">'
                + html.escape(DATA["jobs"][key]["title"])
                + "</a>"
                for key in page["listing_ids"]
            )
            next_button = (
                '<button aria-label="Go to next page">Next</button>' if page["next_visible"] else ""
            )
            result[board_url(org, page["page"] - 1, sort)] = (
                f"<main><p>{page['range']}</p>{links}{next_button}</main>"
            )
    return result


class Captures:
    def __init__(self, tmp_path, replies):
        self.target = tmp_path
        (tmp_path / "http").mkdir()
        self.replies = replies
        self.calls = []
        self.paths = []

    def get(self, url):
        self.calls.append(url)
        text = self.replies[url]
        path = self.target / "http" / f"{len(self.calls):05d}.json"
        artifact = path.with_suffix(".body.gz")
        body = text.encode()
        artifact.write_bytes(gzip.compress(body))
        meta = {
            "phase": {"kind": "listing"},
            "url": url,
            "response_url": url,
            "method": "GET",
            "status_code": 200,
            "body_captured": True,
            "artifact": str(artifact),
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        path.write_text(json.dumps(meta))
        self.paths.append(path)
        return HttpResponse(url, 200, {"Content-Type": "text/html"}, text, body)


def test_actual_81_row_sort_union_is_reproduced_without_rss_as_inventory(tmp_path):
    transport = Captures(tmp_path, documents())
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(), transport))
    jobs = adapter.fetch_jobs()
    assert len(jobs) == 81 and len(transport.calls) == 19
    assert adapter.run_diagnostics.pagination_complete
    walks = adapter.run_diagnostics.zero_fetched_evidence["walks"]
    assert [w["unique_rows"] for w in walks] == [67, 76]
    result = verify_listing(source(), jobs, transport.paths)
    assert result["complete"] and result["reported_total"] == 81 and result["page_count"] == 18
    assert all(j.raw["href"] == j.apply_url and j.description is None for j in jobs)
    # A real job with a literal entity-containing URL must retain its observed
    # href, not get a second entity-decoding pass during JobRecord creation.
    assert (
        next(j for j in jobs if j.external_id == "3452").apply_url
        == DATA["jobs"]["3452"]["detail_url"]
    )


def test_empty_rss_always_attempts_configured_board_but_shell_is_not_zero_proof(tmp_path):
    replies = documents()
    replies[board_url(source(), 0, "relevance")] = "<xweb-rmk-jobs-search></xweb-rmk-jobs-search>"
    transport = Captures(tmp_path, replies)
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(), transport))
    assert adapter.fetch_jobs() == []
    assert len(transport.calls) == 2 and not adapter.run_diagnostics.pagination_complete
    assert "rendered result range" in adapter.run_diagnostics.empty_reason
    assert adapter.run_diagnostics.zero_fetched_evidence["legacy_rss"]["jobs"] == []
    assert not verify_listing(source(), [], transport.paths)["complete"]


def test_cap_returns_observed_subset_and_explicit_incomplete(tmp_path):
    transport = Captures(tmp_path, documents())
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(2), transport))
    jobs = adapter.fetch_jobs()
    assert jobs and len(jobs) < 81 and len(transport.calls) == 3
    assert not adapter.run_diagnostics.pagination_complete
    assert "cap reached" in adapter.run_diagnostics.empty_reason
    assert not verify_listing(source(2), jobs, transport.paths)["complete"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_range",
        "hidden_range",
        "script_range",
        "wrong_range",
        "missing_anchor",
        "hidden_anchor",
        "wrong_id",
        "wrong_host",
        "no_next",
        "wrong_query",
    ],
)
def test_dom_contract_rejects_missing_hidden_wrong_scope(mutation):
    url = board_url(source(), 0, "relevance")
    text = documents()[url]
    if mutation == "missing_range":
        text = text.replace("1 to 10 of 81 results", "")
    if mutation == "hidden_range":
        text = text.replace("<p>", "<p hidden>")
    if mutation == "script_range":
        text = text.replace("<p>", "<script>").replace("</p>", "</script>")
    if mutation == "wrong_range":
        text = text.replace("1 to 10", "11 to 20")
    if mutation == "missing_anchor":
        text = text.replace("<a ", "<span ", 1).replace("</a>", "</span>", 1)
    if mutation == "hidden_anchor":
        text = text.replace("<a ", '<a style="display:none" ', 1)
    if mutation == "wrong_id":
        text = text.replace("3470-en_US", "3470-fr_FR", 1)
    if mutation == "wrong_host":
        text = text.replace("jobs.iadb.org/job/", "other.example/job/", 1)
    if mutation == "no_next":
        text = text.replace('<button aria-label="Go to next page">Next</button>', "")
    if mutation == "wrong_query":
        url += "?keywords=restricted"
    with pytest.raises(ValueError):
        parse_board(text, url, expected_page=0)


def test_aria_hidden_alone_does_not_hide_visually_present_links():
    url = board_url(source(), 0, "relevance")
    text = documents()[url].replace("<a ", '<a aria-hidden="true" ', 1)
    assert len(parse_board(text, url, expected_page=0)["rows"]) == 10


def test_current_title_conflict_between_sorts_stops(tmp_path):
    replies = documents()
    url = board_url(source(), 0, "date")
    key = DATA["walks"][1]["pages"][0]["listing_ids"][0]
    replies[url] = replies[url].replace(html.escape(DATA["jobs"][key]["title"]), "Unrelated title")
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(), Captures(tmp_path, replies)))
    with pytest.raises(ValueError, match="disagree"):
        adapter.fetch_jobs()


def test_browser_composed_dom_must_bind_original_http_and_rendered_sha(tmp_path):
    transport = Captures(tmp_path, documents())
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(), transport))
    jobs = adapter.fetch_jobs()
    # Replace the captured network page with a true shell; the browser receipt
    # independently pins its rendered DOM which the adapter actually consumed.
    path = transport.paths[1]
    meta = json.loads(path.read_text())
    original = documents()[meta["url"]]
    shell = b"<xweb-rmk-jobs-search></xweb-rmk-jobs-search>"
    Path(meta["artifact"]).write_bytes(gzip.compress(shell))
    meta["body_sha256"] = hashlib.sha256(shell).hexdigest()
    path.write_text(json.dumps(meta))
    target = tmp_path / "browser-proof"
    target.mkdir()
    htmlpath = target / "rendered.html"
    htmlpath.write_text(original)
    receipt = {
        "url": meta["url"],
        "html_path": str(htmlpath),
        "html_sha256": hashlib.sha256(original.encode()).hexdigest(),
    }
    (target / "receipt.json").write_text(json.dumps(receipt))
    assert verify_listing(source(), jobs, transport.paths)["complete"]
    htmlpath.write_text(original + "modified")
    assert not verify_listing(source(), jobs, transport.paths)["complete"]


def test_capture_census_rejects_changed_dispatch_href(tmp_path):
    transport = Captures(tmp_path, documents())
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source(), transport))
    jobs = adapter.fetch_jobs()
    jobs[0].raw['href'] = 'https://jobs.iadb.org/job/Wrong/9999-en_US'
    assert not verify_listing(source(), jobs, transport.paths)['complete']
