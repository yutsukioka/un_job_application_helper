"""Public IDB RMK search endpoint observed in the September 2026 browser HAR."""

import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from jobagg.normalize import build_job

URL = "https://jobs.iadb.org/services/recruiting/v1/jobs"
VERSION = "idb_public_search_api_v1"


def request_payload(page, sort):
    return dict(
        locale="en_US",
        pageNumber=page,
        sortBy=sort,
        keywords="",
        location="",
        facetFilters={},
        brand="",
        skills=[],
        categoryId=9638000,
        alertId="",
        rcmCandidateId="",
    )


def parse_page(data, page):
    if not isinstance(data, dict) or type(page) is not int or page < 0:
        raise ValueError("IDB API malformed page")
    total = data.get("totalJobs")
    entries = data.get("jobSearchResult")
    if type(total) is not int or total < 0 or not isinstance(entries, list):
        raise ValueError("IDB API missing total or rows")
    if len(entries) != min(10, max(0, total - page * 10)):
        raise ValueError("IDB API page size disagrees with total")
    rows = []
    for item in entries:
        r = item.get("response") if isinstance(item, dict) else None
        if not isinstance(r, dict):
            raise ValueError("IDB API malformed job response")
        identity, title, slug = (
            r.get("id"),
            r.get("unifiedStandardTitle"),
            r.get("urlTitle"),
        )
        if (
            not isinstance(identity, str)
            or not identity.isascii()
            or not identity.isdigit()
            or not isinstance(title, str) or not title.strip()
            or not isinstance(slug, str) or not slug.strip()
            or not isinstance(r.get("supportedLocales"), list)
            or "en_US" not in r["supportedLocales"]
            or not isinstance(r.get("jobLocationShort", []), list)
            or any(not isinstance(value, str) for value in r.get("jobLocationShort", []))
        ):
            raise ValueError("IDB API malformed job identity/locale")
        url = "https://jobs.iadb.org/job/" + quote(slug, safe="-&;_") + "/" + identity + "-en_US"
        rows.append(dict(external_id=identity, title=title, url=url, fields=r))
    if len({r["external_id"] for r in rows}) != len(rows):
        raise ValueError("IDB API duplicates within a page")
    return total, rows


def reconcile(pages, *, require_total=True):
    totals, rows = set(), {}
    for sort in ("", "date"):
        walk = sorted((p for p in pages if p["sort"] == sort), key=lambda p: p["page"])
        if not walk or [p["page"] for p in walk] != list(range(len(walk))):
            raise ValueError("IDB API missing sort/page")
        if len(walk) != max(1, (walk[0]["total"] + 9) // 10):
            raise ValueError("IDB API unterminated walk")
        for p in walk:
            totals.add(p["total"])
            for r in p["rows"]:
                old = rows.get(r["external_id"])
                if old and any(old[k] != r[k] for k in ("title", "url")):
                    raise ValueError("IDB API identity changed during walk")
                rows[r["external_id"]] = r
    if len(totals) != 1 or (require_total and len(rows) != next(iter(totals))):
        raise ValueError("IDB API unique union does not equal advertised total")
    return rows


def fetch(adapter):
    pages = []
    for sort in ("", "date"):
        for page in range(50):
            data = adapter.post_json(URL, request_payload(page, sort))
            total, rows = parse_page(data, page)
            pages.append(dict(page=page, sort=sort, total=total, rows=rows))
            if (page + 1) * 10 >= total:
                break
    rows = reconcile(pages, require_total=False)
    total = pages[0]["total"]
    complete = len(rows) == total
    d = adapter.run_diagnostics
    d.pages_fetched = len(pages)
    d.total_reported_by_source = total
    d.pagination_complete = complete
    d.list_error_count = 0 if complete else 1
    d.health_status = "ok" if complete else "issue"
    d.zero_fetched_evidence = {"method": VERSION}
    if complete and total == 0:
        d.empty_reason = "verified_total_zero"
    return [
        build_job(
            adapter.source,
            external_id=r["external_id"],
            title=r["title"],
            apply_url=r["url"],
            source_url=r["url"],
            location="; ".join(r["fields"].get("jobLocationShort", [])),
            posted_at=r["fields"].get("unifiedStandardStart"),
            closes_at=r["fields"].get("unifiedStandardEnd"),
            raw={
                "external_id": r["external_id"],
                "title": r["title"],
                "href": r["url"],
                "parser": VERSION,
                "public_search_fields": r["fields"],
            },
        )
        for r in rows.values()
    ]


def verify(source, jobs, capture_paths):
    result = dict(
        complete=False,
        method=VERSION,
        observed_count=len(jobs),
        scope="Unfiltered All Jobs, en_US, two-sort public API union",
        reasons=[],
        capture_paths=[],
    )
    try:
        pages, seen = [], set()
        for name in capture_paths:
            path = Path(name)
            meta = json.loads(path.read_text())
            if not isinstance(meta, dict):
                raise ValueError("IDB API malformed capture metadata")
            if meta.get("url") != URL:
                continue
            req = meta.get("public_pagination_request", {})
            if not isinstance(req, dict):
                raise ValueError("IDB API malformed captured request")
            page, sort = req.get("pageNumber"), req.get("sortBy")
            if (
                type(page) is not int
                or page < 0
                or sort not in ("", "date")
                or req != request_payload(page, sort)
            ):
                raise ValueError("IDB API request scope differs")
            if (sort, page) in seen:
                raise ValueError("IDB API duplicate page capture")
            seen.add((sort, page))
            if (
                meta.get("method") != "POST"
                or meta.get("status_code") != 200
                or meta.get("response_url") != URL
                or not isinstance(meta.get("phase"), dict)
                or meta["phase"].get("kind") != "listing"
                or meta.get("body_captured") is not True
            ):
                raise ValueError("IDB API unsuccessful capture")
            body = gzip.decompress(Path(meta["artifact"]).read_bytes())
            if hashlib.sha256(body).hexdigest() != meta.get("body_sha256"):
                raise ValueError("IDB API captured bytes changed")
            total, rows = parse_page(json.loads(body), page)
            pages.append(dict(page=page, sort=sort, total=total, rows=rows))
            result["capture_paths"].append(
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        rows = reconcile(pages)
        actual = {j.external_id: (j.title, j.apply_url) for j in jobs}
        expected = {key: (r["title"], r["url"]) for key, r in rows.items()}
        if (
            actual != expected
            or len(jobs) != len(rows)
            or any(j.source_id != source.id or j.source_url != j.apply_url for j in jobs)
        ):
            raise ValueError("IDB API jobs differ from captured inventory")
        result.update(
            complete=True,
            reported_total=len(rows),
            page_count=len(pages),
            verified_zero=not rows,
        )
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["reasons"].append(str(exc))
    return result
