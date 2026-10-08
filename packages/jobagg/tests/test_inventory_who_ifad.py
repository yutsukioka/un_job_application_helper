"""Synthetic public structures; no sessions, private archives or provider requests."""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.models import JobRecord, OrganizationSource
from jobagg.pipelines.inventory_checks import source_capability, verify_listing
from jobagg.pipelines.inventory_who_ifad import IFAD_LIST, IFAD_ROUTE, WHO_API


def capture(tmp_path, source, body, index=0, request=None):
    if isinstance(body, dict):
        body = json.dumps(body)
    body = body.encode()
    artifact = tmp_path / f"{index}.gz"
    artifact.write_bytes(gzip.compress(body))
    url = WHO_API if source.id == "who_taleo" else IFAD_LIST
    meta = {
        "source_binding": {"source_id": source.id, "ats_family": source.ats_family},
        "phase": {"kind": "listing", "job_id": None},
        "external_id": None,
        "method": "POST" if request else "GET",
        "status_code": 200,
        "state": "response_captured",
        "body_captured": True,
        "url": url,
        "response_url": url,
        "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "response_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "request_body_sha256": hashlib.sha256(
            json.dumps(request, separators=(",", ":")).encode()
        ).hexdigest()
        if request
        else None,
        "started_at": f"2026-10-08T00:{index:02}:00+00:00",
        "finished_at": f"2026-10-08T00:{index:02}:01+00:00",
        "artifact": str(artifact),
        "body_bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    path = tmp_path / f"{index}.json"
    path.write_text(json.dumps(meta))
    return path


def mutate_body(path, transform):
    meta = json.loads(path.read_text())
    artifact = Path(meta["artifact"])
    data = gzip.decompress(artifact.read_bytes()).decode()
    data = transform(data).encode()
    artifact.write_bytes(gzip.compress(data))
    meta.update(body_bytes=len(data), body_sha256=hashlib.sha256(data).hexdigest())
    path.write_text(json.dumps(meta))


def who(tmp_path, count=65, returned=None):
    source = OrganizationSource(
        "who_taleo",
        "WHO",
        "taleo",
        "https://careers.who.int/careersection/ex/jobsearch.ftl",
        extra={
            "search_api_url": WHO_API,
            "max_pages": 25,
            "search_payload": {
                "pageNo": 1,
                "fieldData": {"valid": True, "fields": {"KEYWORD": "", "LOCATION": ""}},
                "filterSelectionParam": {
                    "searchFilterSelections": [{"id": "JOB_LOCALE", "selectedValues": []}]
                },
            },
        },
    )
    returned = count if returned is None else returned
    paths, jobs = [], []
    for page in range(max(1, (count + 24) // 25)):
        rows = [
            {
                "contestNo": str(2600000 + n),
                "jobId": str(400000 + n),
                "column": ["Officer", str(2600000 + n)],
            }
            for n in range(page * 25, min((page + 1) * 25, returned))
        ]
        body = {
            "careerSectionUnAvailable": False,
            "pagingData": {"totalCount": count, "pageSize": 25, "currentPageNo": page + 1},
            "requisitionList": rows,
            "facetResults": [
                {
                    "id": "JOB_LOCALE",
                    "facetValueResults": [{"id": "en", "quantity": str(count)}] if count else [],
                }
            ],
        }
        request = deepcopy(source.extra["search_payload"])
        request["pageNo"] = page + 1
        paths.append(capture(tmp_path, source, body, page, request))
        jobs.extend(
            JobRecord(
                source_id=source.id,
                org_id=source.id,
                ats_family=source.ats_family,
                external_id=r["contestNo"],
                title="Officer",
                apply_url=source.base_url,
                raw=r,
            )
            for r in rows
        )
    return source, jobs, paths


def ifad(tmp_path, count=11):
    source = OrganizationSource(
        "ifad_peoplesoft",
        "IFAD",
        "peoplesoft",
        "https://job.ifad.org" + IFAD_ROUTE,
        extra={"listing_url": IFAD_LIST, "public_job_deeplinks": True},
    )
    rows = "".join(
        f"""<li class="ps_grid-row psc_rowact psc_disabled" id="HRS_AGNT_RSLT_I$0_row_{n}">
      <span id="HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID${n}">{37000 + n}</span>
      <span id="SCH_JOB_TITLE${n}">Officer {n}</span></li>"""
        for n in range(count)
    )
    html = f"""<html><form id="HRS_CG_SEARCH_FL" name="win0" action="https://job.ifad.org{IFAD_ROUTE}">
      <input id="HRS_SCH_WRK_HRS_SCH_TEXT100" value="">
      <input id="PTS_SELECT$0" type="checkbox" value="Y"><input id="PTS_SELECT$chk$0" type="hidden" value="N">
      <div id="win0divHRS_SCH_WRK_FLU_HRS_SES_CNTS_MSG"><b>{count}</b> jobs found.</div>
      <div id="win0divHRS_AGNT_RSLT_I$0"><div id="win0divHRS_AGNT_RSLT_Irowcnt$0">{count} rows</div><ul>{rows}</ul></div>
      </form></html>"""
    jobs = [
        JobRecord(
            source_id=source.id,
            org_id=source.id,
            ats_family=source.ats_family,
            external_id=str(37000 + n),
            title=f"Officer {n}",
            apply_url=source.base_url,
            raw={"job_id": str(37000 + n)},
        )
        for n in range(count)
    ]
    return source, jobs, [capture(tmp_path, source, html)]


@pytest.mark.parametrize("factory", [who, ifad])
def test_complete_native_census_and_verified_empty(tmp_path, factory):
    args = factory(tmp_path)
    result = verify_listing(*args)
    assert result["complete"] and result["reported_total"] == len(args[1])
    assert source_capability(args[0])["enumeration_contract"] == result["method"]
    assert result["capture_paths"] and result["started_at"] and result["finished_at"]
    empty = verify_listing(*factory(tmp_path, count=0))
    assert empty["complete"] and empty["verified_zero"]
    assert not verify_listing(args[0], [], [])["complete"]


def test_actual_who_65_advertised_62_returned_cannot_prove_absence(tmp_path):
    result = verify_listing(*who(tmp_path, 65, 62))
    assert result["observed_count"] == 62 and result["reported_total"] == 65
    assert not result["complete"]
    assert "row count" in result["reasons"][0]


@pytest.mark.parametrize(
    "damage",
    [
        "page",
        "total",
        "size",
        "locale",
        "locale_count",
        "duplicate",
        "native_duplicate",
        "display_id",
        "unavailable",
        "paging_null",
        "locale_null",
        "truncated",
    ],
)
def test_who_native_pagination_and_identity_negatives(tmp_path, damage):
    args = who(tmp_path)

    def change(text):
        data = json.loads(text)
        if damage == "page":
            data["pagingData"]["currentPageNo"] = 1
        if damage == "total":
            data["pagingData"]["totalCount"] = 66
        if damage == "size":
            data["pagingData"]["pageSize"] = 30
        if damage == "locale":
            data["facetResults"][0]["facetValueResults"][0]["id"] = "fr"
        if damage == "locale_count":
            data["facetResults"][0]["facetValueResults"][0]["quantity"] = "60"
        if damage == "locale_null":
            data["facetResults"][0]["facetValueResults"] = [None]
        if damage == "paging_null":
            data["pagingData"] = None
        if damage == "duplicate":
            data["requisitionList"][0] = deepcopy(args[1][0].raw)
        if damage == "native_duplicate":
            data["requisitionList"][0]["jobId"] = args[1][0].raw["jobId"]
        if damage == "display_id":
            data["requisitionList"][0]["column"][1] = "9999999"
        if damage == "unavailable":
            data["careerSectionUnAvailable"] = True
        if damage == "truncated":
            data["requisitionList"].pop()
        return json.dumps(data)

    mutate_body(args[2][1], change)
    assert not verify_listing(*args)["complete"]


@pytest.mark.parametrize("factory", [who, ifad])
@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "binding_null",
        "phase_null",
        "detail",
        "status",
        "state",
        "captured",
        "request_hash",
        "response_hash",
        "body_hash",
        "size",
        "url_filter",
        "response_host",
        "timestamp",
        "overlap",
        "request_body",
        "gzip",
    ],
)
def test_bound_capture_negatives(tmp_path, factory, damage):
    args = factory(tmp_path)
    path = args[2][-1]
    meta = json.loads(path.read_text())
    if damage == "source":
        meta["source_binding"]["source_id"] = "other"
    if damage == "binding_null":
        meta["source_binding"] = None
    if damage == "phase_null":
        meta["phase"] = None
    if damage == "detail":
        meta["phase"]["kind"] = "detail"
    if damage == "status":
        meta["status_code"] = 403
    if damage == "state":
        meta["state"] = "failed"
    if damage == "captured":
        meta["body_captured"] = False
    if damage == "request_hash":
        meta["request_url_sha256"] = "bad"
    if damage == "response_hash":
        meta["response_url_sha256"] = "bad"
    if damage == "body_hash":
        meta["body_sha256"] = "bad"
    if damage == "size":
        meta["body_bytes"] -= 1
    if damage == "url_filter":
        meta["url"] += "&KEYWORD=filtered"
    if damage == "response_host":
        meta["response_url"] = (
            meta["response_url"].replace(".org", ".invalid").replace(".int", ".invalid")
        )
    if damage == "timestamp":
        meta["started_at"] = "2026-10-08T00:00:00"
    if damage == "overlap":
        meta["started_at"] = "2026-10-09T00:00:00+00:00"
    if damage == "request_body":
        meta["request_body_sha256"] = "unexpected"
    if damage == "gzip":
        Path(meta["artifact"]).write_bytes(b"\x1f\x8b")
    path.write_text(json.dumps(meta))
    assert not verify_listing(*args)["complete"]


@pytest.mark.parametrize(
    "damage", ["missing_page", "extra_page", "filtered", "portal", "parsed_subset", "parsed_native"]
)
def test_who_scope_and_parsed_set_negatives(tmp_path, damage):
    source, jobs, paths = who(tmp_path)
    if damage == "missing_page":
        paths.pop(1)
    if damage == "extra_page":
        paths.append(paths[-1])
    if damage == "filtered":
        source.extra["search_payload"]["fieldData"]["fields"]["KEYWORD"] = "audit"
    if damage == "portal":
        source.extra["search_api_url"] = WHO_API.replace("101430233", "123")
    if damage == "parsed_subset":
        jobs.pop()
    if damage == "parsed_native":
        jobs[0].raw = {**jobs[0].raw, "jobId": "123"}
    assert not verify_listing(source, jobs, paths)["complete"]


@pytest.mark.parametrize(
    "before,after",
    [
        ("<b>11</b>", "<b>12</b>"),
        ("11 rows", "10 rows"),
        (
            'id="HRS_SCH_WRK_HRS_SCH_TEXT100" value=""',
            'id="HRS_SCH_WRK_HRS_SCH_TEXT100" value="audit"',
        ),
        ('type="checkbox" value="Y"', 'type="checkbox" value="Y" checked'),
        ('type="hidden" value="N"', 'type="hidden" value="Y"'),
        (">37001<", ">37000<"),
        (">37000<", ">unknown<"),
        ('id="HRS_AGNT_RSLT_I$0_row_0"', 'id="HRS_AGNT_RSLT_I$0_row_1"'),
        ('id="HRS_AGNT_RSLT_I$0_row_0"', 'id="HRS_AGNT_RSLT_I$0_row_0" hidden'),
        ('id="SCH_JOB_TITLE$0"', 'id="SCH_JOB_TITLE$0" style="display:none"'),
        ("</ul>", '<a title="Next">Next</a></ul>'),
        ("</form>", ""),
        ('name="win0"', 'name="win9"'),
        ("<ul>", "<ul></form><form>"),
        ('id="SCH_JOB_TITLE$0"', 'id="SCH_JOB_TITLE$0" id="other"'),
        (
            '<div id="win0divHRS_SCH_WRK_FLU_HRS_SES_CNTS_MSG">',
            '<script id="win0divHRS_SCH_WRK_FLU_HRS_SES_CNTS_MSG">',
        ),
    ],
)
def test_ifad_native_dom_negatives(tmp_path, before, after):
    args = ifad(tmp_path)
    mutate_body(args[2][0], lambda data: data.replace(before, after, 1))
    assert not verify_listing(*args)["complete"]


def test_ifad_cap_and_parser_disagreement_remain_incomplete(tmp_path):
    result = verify_listing(*ifad(tmp_path, 100))
    assert not result["complete"] and "100-result ceiling" in result["reasons"][0]
    source, jobs, paths = ifad(tmp_path)
    jobs[0].title = "Unrelated title"
    assert not verify_listing(source, jobs, paths)["complete"]
    source, jobs, paths = ifad(tmp_path)
    assert not verify_listing(source, jobs[:-1], paths)["complete"]
    assert not verify_listing(source, jobs, paths * 2)["complete"]


def test_ifad_unrelated_script_counts_do_not_certify_zero(tmp_path):
    source, _, paths = ifad(tmp_path, 0)
    mutate_body(paths[0], lambda _: "<script>0 jobs found. 0 rows</script>")
    assert not verify_listing(source, [], paths)["complete"]


@pytest.mark.parametrize("factory,complete", [(ifad, True), (who, False)])
@pytest.mark.parametrize("status", ["pending", "unavailable_pending_inventory", "blocked"])
def test_worker_absence_requires_complete_census_and_preserves_text_history(
    tmp_path, monkeypatch, request, factory, complete, status
):
    from dataclasses import asdict
    from types import SimpleNamespace
    from jobagg.models import SourceRunDiagnostics
    from jobagg.remediation_worker import dump

    worker, _, calls, _ = request.getfixturevalue("worker_setup")
    worker.initialize()
    target = tmp_path / "native-census"
    http = target / "http"
    http.mkdir(parents=True)
    source, jobs, _ = factory(http, **({"returned": 62} if factory is who else {}))
    worker.by_id[source.id] = source
    old = deepcopy(jobs[0])
    old.external_id = "9999999"
    old.description = "Retained complete historical duties and qualifications. " * 30
    worker.db.upsert_job(old)
    before = worker.db.get_job(old.identity_key())
    with worker.db.connection_scope() as conn:
        conn.execute(
            "INSERT INTO remediation_sources(source_id,next_list_at,last_service) VALUES(?,0,0)",
            (source.id,),
        )
        key = worker.enqueue(
            conn, source.id, "detail", old.external_id, {"listing": asdict(old)}, due=123
        )
        conn.execute(
            "UPDATE remediation_tasks SET status=?,attempts=7,receipt=?,last_error='retained evidence' WHERE task_id=?",
            (status, dump({"incomplete_response_count": 2}), key),
        )
    # The unavailable receipt validator is independently covered by source-outcome
    # tests. Here its timestamp lets the real absence branch consume the new census.
    monkeypatch.setattr(
        worker,
        "verified_unavailable_receipt",
        lambda _: {"observed_at": "2026-10-07T00:00:00+00:00"},
    )
    monkeypatch.setattr(worker, "finish", lambda *a, **kw: None)
    adapter = SimpleNamespace(
        fetch_jobs=lambda: jobs, run_diagnostics=SourceRunDiagnostics(source_id=source.id)
    )
    result = worker.do_listing(source, adapter, SimpleNamespace(), target, {}, "fixture")
    assert result["independent_enumeration"] is complete
    with worker.db.connection_scope() as conn:
        after = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
    assert after["status"] == ("not_observed" if complete and status != "blocked" else status)
    assert after["attempts"] == 7
    assert json.loads(after["receipt"])["incomplete_response_count"] == 2
    current = worker.db.get_job(old.identity_key())
    assert current["description"] == before["description"]
    assert current["first_seen_at"] == before["first_seen_at"]
    assert current["status"] != "closed"
    assert calls == []


from test_remediation_worker import setup as worker_setup  # noqa: E402,F401


@pytest.mark.parametrize("factory,complete", [(ifad, True), (who, False)])
@pytest.mark.parametrize("prior_proof_complete", [True, False])
def test_publication_rechecks_native_census_and_cannot_upgrade_old_incomplete_receipt(
    tmp_path, factory, complete, prior_proof_complete
):
    from dataclasses import asdict
    from datetime import datetime, timezone
    from jobagg.db import JobDatabase
    from jobagg.pipelines.live_inventory import plan_frames

    http = tmp_path / "capture" / "http"
    http.mkdir(parents=True)
    source, jobs, paths = factory(http, **({"returned": 62} if factory is who else {}))
    proof = verify_listing(source, jobs, paths)
    proof["complete"] = prior_proof_complete
    observed = datetime.now(timezone.utc)
    frame = http.parent / "listing.json"
    frame.write_text(
        json.dumps(
            {
                "source_id": source.id,
                "observed_at": observed.isoformat(),
                "jobs": [asdict(j) for j in jobs],
            },
            default=lambda x: x.isoformat(),
        )
    )
    worker, target, live = [
        JobDatabase(tmp_path / name) for name in ("worker.sqlite3", "source.sqlite3", "all.sqlite3")
    ]
    for db in (worker, target, live):
        db.initialize()
    with worker.connect() as conn:
        conn.executescript("""CREATE TABLE remediation_sources(source_id TEXT,last_list_at REAL,listing_ids TEXT,listing_proof TEXT);
        CREATE TABLE remediation_tasks(source_id TEXT,kind TEXT,status TEXT,receipt TEXT);""")
        conn.execute(
            "INSERT INTO remediation_sources VALUES(?,?,?,?)",
            (
                source.id,
                observed.timestamp(),
                json.dumps([j.identity_key() for j in jobs]),
                json.dumps(proof),
            ),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES(?,?,?,?)",
            (
                source.id,
                "listing",
                "done",
                json.dumps(
                    {
                        "frame_path": str(frame),
                        "frame_sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                        "enumeration": proof,
                    }
                ),
            ),
        )
    with worker.connect() as wc, live.connect() as lc:
        frames, rejected = plan_frames(
            wc, lc, {source.id: source}, {source.id: target.path}, limit=5
        )
    assert not rejected and len(frames) == 1
    assert frames[0]["inventory_complete"] is (complete and prior_proof_complete)


@pytest.mark.parametrize("factory", [who, ifad])
def test_ambiguous_metadata_and_bounded_capture_reads_fail_closed(tmp_path, factory, monkeypatch):
    from jobagg.pipelines import inventory_who_ifad as contract

    args = factory(tmp_path)
    path = args[2][0]
    saved = path.read_text()
    path.write_text(saved.replace('"status_code": 200', '"status_code": 403, "status_code": 200'))
    assert not verify_listing(*args)["complete"]
    path.write_text(saved)
    monkeypatch.setattr(contract, "MAX_BODY", 100)
    assert not verify_listing(*args)["complete"]


def test_who_ambiguous_response_member_is_not_a_census(tmp_path):
    args = who(tmp_path)
    mutate_body(
        args[2][0], lambda x: x.replace('"totalCount": 65', '"totalCount": 99, "totalCount": 65')
    )
    assert not verify_listing(*args)["complete"]


@pytest.mark.parametrize(
    "advanced",
    [None, {"searchFilterSelections": [{"id": "ORGANIZATION", "selectedValues": ["restricted"]}]}],
)
def test_who_advanced_filters_cannot_certify_unfiltered_board(tmp_path, advanced):
    source, jobs, paths = who(tmp_path)
    source.extra["search_payload"]["advancedSearchFiltersSelectionParam"] = advanced
    # Also bind the changed request: refusing it must be a scope decision, not
    # an incidental request-hash mismatch.
    for page, path in enumerate(paths, 1):
        meta = json.loads(path.read_text())
        request = deepcopy(source.extra["search_payload"])
        request["pageNo"] = page
        meta["request_body_sha256"] = hashlib.sha256(
            json.dumps(request, separators=(",", ":")).encode()
        ).hexdigest()
        path.write_text(json.dumps(meta))
    result = verify_listing(source, jobs, paths)
    assert not result["complete"] and "advanced search" in result["reasons"][0]


@pytest.mark.parametrize("attribute", ["id", "class", "style", "title", "aria-label"])
def test_ifad_missing_attribute_value_returns_incomplete_not_exception(tmp_path, attribute):
    args = ifad(tmp_path)
    mutate_body(args[2][0], lambda data: data.replace("<html>", f"<html {attribute}>"))
    result = verify_listing(*args)
    assert not result["complete"] and "required value" in result["reasons"][0]


@pytest.mark.parametrize(
    "declaration,complete", [("<![foo]>", False), ("<![if IE]><![endif]>", True)]
)
def test_ifad_declarations_do_not_escape_verification_or_break_valid_markup(
    tmp_path, declaration, complete
):
    from jobagg.adapters.base import AdapterContext
    from jobagg.adapters.peoplesoft import PeopleSoftAdapter

    source, _, paths = ifad(tmp_path)
    mutate_body(paths[0], lambda data: declaration + data)
    meta = json.loads(paths[0].read_text())
    html = gzip.decompress(Path(meta["artifact"]).read_bytes()).decode()
    jobs = PeopleSoftAdapter(AdapterContext(source, None)).parse_listing_html(html)
    assert len(jobs) == 11
    result = verify_listing(source, jobs, paths)
    assert result["complete"] is complete
    if not complete:
        assert "malformed declared markup" in result["reasons"][0]
