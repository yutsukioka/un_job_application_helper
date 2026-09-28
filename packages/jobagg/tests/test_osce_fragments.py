import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

import pytest

from jobagg.adapters.osce_inventory import captured_scope, reconcile
from jobagg.osce_fragments import (
    DATA_ROUTE,
    csrf_token,
    validate_csrf_header,
    page_url,
    request_url,
    result_html,
    session_parts,
    site_name,
    validate_request,
    verify_captures,
)
from jobagg.pipelines.host_recovery import authorize_osce_data_probe, host_eligibility
from test_osce_native_browser import inputs, native as native

FIXTURE = json.loads((Path(__file__).parent / "fixtures/osce/fragments/pages.json").read_text())


def first_html():
    return (
        '<html><head><style id="antiClickjacking">body{display:none !important;}</style></head><body>'
        '<input type="hidden" name="tsstoken" id="tsstoken" value="current-fixture-token">'
        + FIXTURE["pages"][0]
        + '<script>var TVAPP={site:{short_name:"'
        + FIXTURE["site"]
        + '"}};fetch("/forbidden");</script><script src="/js-dict"></script></body></html>'
    )


def test_har_fragments_reconcile_all_45_and_history_urls():
    pages = [
        {"number": i, "url": page_url(FIXTURE["session"], i), "html": h, "scope": captured_scope(h)}
        for i, h in enumerate(FIXTURE["pages"], 1)
    ]
    rows, total = reconcile(pages)
    assert total == 45 and len({r[0] for r in rows}) == 45
    assert site_name(first_html()) == FIXTURE["site"]
    assert session_parts(pages[2]["url"]) == (FIXTURE["session"], 3)
    pages[2]["url"] = page_url(FIXTURE["session"], 4)
    with pytest.raises(ValueError, match="URL page index"):
        reconcile(pages)


@pytest.mark.parametrize(
    "change", ["host", "extra", "duplicate", "session", "page", "body", "method", "site"]
)
def test_fragment_request_contract_rejects_unbound_requests(change):
    args = dict(session="123", site="default1", page=2)
    url = request_url(**args, uid=1)
    method, body = "POST", None
    if change == "host":
        url = url.replace("vacancies.osce.org", "other.example")
    if change == "extra":
        url += "&delete=true"
    if change == "duplicate":
        url += "&page_index=2"
    if change == "session":
        url = url.replace("JobSearch.id=123", "JobSearch.id=999")
    if change == "page":
        url = url.replace("page_index=2", "page_index=3")
    if change == "body":
        body = b"action=apply"
    if change == "method":
        method = "GET"
    if change == "site":
        url = url.replace("site-name=default1", "site-name=other")
    with pytest.raises(ValueError):
        validate_request(url, method, body, **args)


def test_result_envelope_page_and_scope_are_required():
    text = json.dumps({"Status": "OK", "Result": FIXTURE["pages"][1]})
    assert result_html(text, 2) == FIXTURE["pages"][1]
    for bad in [text.replace('"OK"', '"ERROR"'), text.replace("All jobs", "Some jobs")]:
        with pytest.raises(ValueError):
            result_html(bad, 2)
    with pytest.raises(ValueError):
        result_html(text, 3)


def capture_bundle(tmp_path):
    pages, paths = [], []
    for number, fragment in enumerate(FIXTURE["pages"], 1):
        html = first_html() if number == 1 else fragment
        url = (
            page_url(FIXTURE["session"], 1)
            if number == 1
            else request_url(FIXTURE["session"], FIXTURE["site"], number, 1)
        )
        raw = (html if number == 1 else json.dumps({"Status": "OK", "Result": html})).encode()
        path = tmp_path / f"{number:05d}.json"
        artifact = path.with_suffix(".body.gz")
        artifact.write_bytes(gzip.compress(raw))
        path.write_text(
            json.dumps(
                {
                    "status_code": 200,
                    "state": "response_captured",
                    "phase": {"kind": "listing"},
                    "transport": "chromium_cdp_native_v1",
                    "body_captured": True,
                    "url": url,
                    "response_url": url,
                    "method": "GET" if number == 1 else "POST",
                    "artifact": str(artifact),
                    "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "transport_diagnostics": {
                        "request_body_bytes": 0,
                        "csrf_observation": {
                            "available": True,
                            "present": True,
                            "matches_document": True,
                        },
                    },
                }
            )
        )
        paths.append(path)
        pages.append(
            {
                "number": number,
                "url": page_url(FIXTURE["session"], number),
                "request_url": url,
                "capture_path": str(path),
                "html": html,
                "scope": captured_scope(html),
            }
        )
    return pages, paths


def bundle(pages):
    return (
        '<script type="application/json" id="jobagg-osce-inventory">'
        + json.dumps(pages)
        + "</script>"
    )


def test_independent_verification_binds_every_page_to_response(tmp_path):
    pages, paths = capture_bundle(tmp_path)
    assert len(verify_captures(bundle(pages), paths)) == 5
    for change in ["html", "capture", "request", "logical_url"]:
        bad = copy.deepcopy(pages)
        if change == "html":
            bad[2]["html"] += "changed"
        if change == "capture":
            bad[2]["capture_path"] = bad[1]["capture_path"]
        if change == "request":
            bad[2]["request_url"] += "&x=1"
        if change == "logical_url":
            bad[2]["url"] = bad[1]["url"]
        with pytest.raises(ValueError):
            verify_captures(bundle(bad), paths)
    paths[1].with_suffix(".body.gz").write_bytes(gzip.compress(b"changed"))
    with pytest.raises(ValueError, match="hash"):
        verify_captures(bundle(pages), paths)


def test_worker_verification_requires_bound_data_receipt(tmp_path):
    from jobagg.adapters.osce_inventory import parse_bundle
    from jobagg.pipelines.inventory_checks import verify_listing

    (tmp_path / "http").mkdir()
    pages, paths = capture_bundle(tmp_path / "http")
    source, _, _, _ = inputs()
    html = bundle(pages)
    jobs, _, _ = parse_bundle(source, html)
    browser = tmp_path / "browser-fixture"
    browser.mkdir()
    rendered = browser / "rendered.html"
    rendered.write_text(html)
    receipt = {
        "url": source.extra["listing_url"],
        "contract": source.extra["browser_render"],
        "html_path": str(rendered),
        "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
    }
    receipt_path = browser / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    proof = verify_listing(source, jobs, paths)
    assert proof["complete"] and proof["reported_total"] == 45
    assert len(proof["capture_paths"]) == 6
    assert not verify_listing(source, jobs, paths[:-1])["complete"]
    receipt["contract"] = {"inventory": "osce_full_search_v1"}
    receipt_path.write_text(json.dumps(receipt))
    assert not verify_listing(source, jobs, paths)["complete"]


@pytest.mark.parametrize(
    "outcome",
    [
        "complete",
        "denied",
        "wrong_page",
        "missing_token",
        "duplicate_token",
        "wrong_header",
        "missing_header",
    ],
)
def test_real_native_fragment_route_omits_scripts_and_keeps_fresh_cookies(
    native, monkeypatch, outcome
):
    browser, origin, replies, calls = native
    session = FIXTURE["session"]
    pattern = re.compile(re.escape(origin) + r"/jobs/search/\d+(?:/page\d+)?/?")
    monkeypatch.setattr("jobagg.osce_native_browser.SESSION", pattern)
    monkeypatch.setattr("jobagg.adapters.osce_inventory.SESSION", pattern)
    browser.contract.update(data_route=DATA_ROUTE, ready_selector=".number_of_results")
    browser.capture.phase = {"kind": "listing"}
    replies["/jobs/search/"] = (
        307,
        [
            ("Location", "/jobs/search/" + session),
            ("Set-Cookie", "JSESSIONID=fixture-a; Path=/; Secure; HttpOnly; SameSite=None"),
            (
                "Set-Cookie",
                "ORA_OTSS_SESSION_ID=fixture-b; Path=/; Secure; HttpOnly; SameSite=None",
            ),
        ],
        "",
    )
    html = first_html()
    if outcome == "missing_token":
        html = html.replace('id="tsstoken"', 'id="other"').replace(
            'name="tsstoken"', 'name="other"'
        )
    if outcome == "duplicate_token":
        html += '<input type="hidden" name="tsstoken" id="tsstoken" value="another-token">'
    if outcome in {"wrong_header", "missing_header"}:
        from playwright.async_api import Page

        original = Page.evaluate

        async def evaluate(page, expression, arg=None):
            if "const response = await fetch(args.url" in expression:
                if outcome == "wrong_header":
                    arg = {**arg, "token": "wrong-fixture-token"}
                else:
                    expression = expression.replace(", 'tss-token':args.token", "")
            return await original(page, expression, arg)

        monkeypatch.setattr(Page, "evaluate", evaluate)
    replies["/jobs/search/" + session] = (200, {"Content-Type": "text/html"}, html)

    def fragment(handler):
        assert handler.command == "POST"
        assert handler.headers.get("Content-Length") == "0"
        assert handler.headers.get("X-Requested-With") == "XMLHttpRequest"
        assert handler.headers.get("Origin") == origin
        assert handler.headers.get("tss-token") == "current-fixture-token"
        assert "JSESSIONID=fixture-a" in handler.headers["Cookie"]
        assert "ORA_OTSS_SESSION_ID=fixture-b" in handler.headers["Cookie"]
        query = dict(parse_qsl(urlsplit(handler.path).query))
        number = int(query["page_index"])
        validate_request(
            origin + handler.path,
            "POST",
            None,
            session=session,
            site=FIXTURE["site"],
            page=number,
            origin=origin,
        )
        if outcome == "denied":
            return 403, {"Content-Type": "text/plain"}, "Forbidden"
        if outcome == "wrong_page":
            return (
                200,
                {"Content-Type": "text/plain"},
                json.dumps({"Status": "OK", "Result": FIXTURE["pages"][0]}),
            )
        return (
            200,
            {"Content-Type": "text/plain;charset=UTF-8"},
            json.dumps({"Status": "OK", "Result": FIXTURE["pages"][number - 1]}),
        )

    replies["/ajax/content/job_results"] = fragment
    if outcome in {"missing_token", "duplicate_token", "wrong_header", "missing_header"}:
        with pytest.raises(Exception, match="CSRF"):
            browser.render(origin + "/jobs/search/")
        assert len(calls) == 2  # Invalid tokens stop before the POST is dispatched.
        return
    if outcome != "complete":
        with pytest.raises(Exception, match="403|page index"):
            browser.render(origin + "/jobs/search/")
        assert len(calls) == 3
        records = [
            json.loads(p.read_text())
            for p in sorted((browser.capture.target / "http").glob("*.json"))
        ]
        assert [r["status_code"] for r in records] == [
            307,
            200,
            403 if outcome == "denied" else 200,
        ]
        if outcome == "denied":
            assert records[-1]["failure_category"] == "access_denied"
        return
    response = browser.render(origin + "/jobs/search/")
    from jobagg.adapters.osce_inventory import parse_bundle

    source, _, _, _ = inputs()
    jobs, total, pages = parse_bundle(source, response.text)
    assert total == len(jobs) == 45 and pages == 5 and len(calls) == 6
    assert not any("/js-dict" in p or "/forbidden" in p for p, _ in calls)
    records = [
        json.loads(p.read_text()) for p in sorted((browser.capture.target / "http").glob("*.json"))
    ]
    assert [r["method"] for r in records] == ["GET", "GET", "POST", "POST", "POST", "POST"]
    assert all(
        "JSESSIONID=fixture-a" in cookie and "ORA_OTSS_SESSION_ID=fixture-b" in cookie
        for _, cookie in calls[1:]
    )
    # CDP may omit a new extra-info event for the fulfilled redirect hop.
    redirected = records[1]["transport_diagnostics"]["cookie_observation"]
    assert redirected == {"available": False} or redirected["sent_cookie_names"] == [
        "JSESSIONID",
        "ORA_OTSS_SESSION_ID",
    ]
    for r in records[2:]:
        assert r["transport_diagnostics"]["csrf_observation"] == {
            "available": True,
            "present": True,
            "matches_document": True,
        }
        assert r["transport_diagnostics"]["cookie_observation"]["sent_cookie_names"] == [
            "JSESSIONID",
            "ORA_OTSS_SESSION_ID",
        ]
    assert "fixture-a" not in json.dumps(records) and "fixture-b" not in json.dumps(records)
    assert "current-fixture-token" not in json.dumps(records)
    assert browser._csrf_token is None


@pytest.mark.parametrize(
    "field",
    [
        "",
        '<input id="tsstoken">',
        '<input type="hidden" id="tsstoken" name="tsstoken" value="">',
        '<input type="hidden" id="tsstoken" name="tsstoken" value="bad&#10;header">',
        '<input type="hidden" id="tsstoken" name="tsstoken" value="one" value="two">',
    ],
)
def test_csrf_input_rejects_missing_or_unsafe_values(field):
    with pytest.raises(ValueError, match="CSRF"):
        csrf_token(field)


def test_csrf_field_decoding_and_case_insensitive_header_binding():
    token = csrf_token('<input type="hidden" id="tsstoken" name="tsstoken" value="a&#43;b/==">')
    assert token == "a+b/=="
    validate_csrf_header({"TSS-Token": token}, token)
    for headers in [{}, {"tss-token": "wrong"}, {"tss-token": token, "TSS-TOKEN": token}]:
        with pytest.raises(ValueError, match="CSRF"):
            validate_csrf_header(headers, token)


def test_data_recovery_is_scoped_and_single_attempt():
    source, state, evidence, _ = inputs()
    evidence.update(
        url="https://vacancies.osce.org/js-dict?v=example", transport="chromium_cdp_native_v1"
    )
    review = {
        "observation": "har_verified_full_listing",
        "complete_captured_listing": True,
        "har_sha256": "a" * 64,
        "observed_at": 150,
    }
    kwargs = dict(owner="owner", now=200, expires_at=700, access_review=review)
    updated = authorize_osce_data_probe(state, evidence, source, **kwargs)
    assert updated["stopped"] and updated["last_request_at"] == state["last_request_at"]
    assert host_eligibility(updated, 201, probe_owner="owner")["allowed"]
    assert not host_eligibility(updated, 201, probe_owner="worker")["allowed"]
    assert not host_eligibility(updated, 701, probe_owner="owner")["allowed"]
    assert not host_eligibility({**updated, "evidence": "new"}, 201, probe_owner="owner")["allowed"]
    with pytest.raises(ValueError, match="already attempted"):
        authorize_osce_data_probe(updated, evidence, source, **kwargs)
    for patch in [
        {"url": source.base_url},
        {"method": "POST"},
        {"phase": {"kind": "detail"}},
        {"status_code": 429},
    ]:
        with pytest.raises(ValueError):
            authorize_osce_data_probe(state, {**evidence, **patch}, source, **kwargs)
