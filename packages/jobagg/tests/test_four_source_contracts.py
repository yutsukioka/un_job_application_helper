import gzip
import hashlib
import json
from pathlib import Path
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter
from jobagg.adapters.icddrb import ICDDRBAdapter
from jobagg.adapters.itcilo_public import parse_itcilo_board
from jobagg.models import OrganizationSource
from jobagg.pipelines.inventory_checks import verify_listing
from test_csod_inventory_recovery import page


def capture(tmp_path, name, url, payload, request=None):
    content = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
    artifact = tmp_path / (name + ".gz")
    artifact.write_bytes(gzip.compress(content))
    meta = {
        "method": "POST" if request else "GET",
        "url": url,
        "response_url": url,
        "status_code": 200,
        "body_captured": True,
        "artifact": str(artifact),
        "body_sha256": hashlib.sha256(content).hexdigest(),
    }
    if request:
        meta["public_pagination_request"] = request
        meta["request_body_sha256"] = hashlib.sha256(
            json.dumps(request, separators=(",", ":")).encode()
        ).hexdigest()
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps(meta))
    return path


def test_csod_independent_capture_set_rejects_missing_pages_and_wrong_scope(tmp_path):
    source = OrganizationSource(
        "worldbank_csod",
        "WBG",
        "csod",
        "https://example.org",
        extra={
            "api_url": "https://example.org/api",
            "page_size": 2,
            "search_payload": {"careerSiteId": 1},
        },
    )
    adapter = CSODAdapter(AdapterContext(source, None))
    pages = [page(3, 1, 2), page(3, 3)]
    jobs = sum([adapter.parse_jobs(p) for p in pages], [])
    paths = [
        capture(
            tmp_path,
            str(i),
            source.extra["api_url"],
            p,
            {"careerSiteId": 1, "pageNumber": i, "pageSize": 2},
        )
        for i, p in enumerate(pages, 1)
    ]
    assert verify_listing(source, jobs, paths)["complete"]
    assert not verify_listing(source, jobs, paths[:1])["complete"]
    source.extra["search_payload"]["searchText"] = "changed"
    assert not verify_listing(source, jobs, paths)["complete"]


@pytest.mark.parametrize("sid", ["icddrb_custom_html", "itcilo_custom_html"])
def test_table_contract_reconciles_all_categories_and_rejects_missing_id(tmp_path, sid):
    ic = sid == "icddrb_custom_html"
    url = "https://career.icddrb.org/" if ic else "https://jobs.itcilo.org/"
    source = OrganizationSource(sid, sid, sid, url, extra={"fetch_details": False})
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / ("icddrb/all_20260918.html" if ic else "itcilo/current_listing_20260913.html")
    )
    body = fixture.read_text()
    if ic:
        adapter = ICDDRBAdapter(AdapterContext(source, None))
        adapter.fetch_text = lambda _: body
        jobs = adapter.fetch_jobs()
    else:
        jobs, _ = parse_itcilo_board(source, body, url)
    paths = [capture(tmp_path, "board", url, body)]
    assert verify_listing(source, jobs, paths)["complete"]
    assert not verify_listing(source, jobs[:-1], paths)["complete"]
    paths = [capture(tmp_path, "board", url, body + "<button>Load more</button>")]
    assert not verify_listing(source, jobs, paths)["complete"]
