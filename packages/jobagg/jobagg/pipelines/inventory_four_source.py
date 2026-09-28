"""Captured full-inventory contracts for the September four-source repair."""

import gzip
import hashlib
import json
from pathlib import Path

from jobagg.pipelines.inventory_api_contracts import require, jobs_by_id, raw_contains

CSOD_FIELDS = frozenset(
    {
        "careerSiteId",
        "careerSitePageId",
        "pageNumber",
        "pageSize",
        "cultureId",
        "cultureName",
        "searchText",
        "states",
        "countryCodes",
        "cities",
        "placeID",
        "radius",
        "postingsWithinDays",
        "customFieldCheckboxKeys",
        "customFieldDropdowns",
        "customFieldRadios",
    }
)


def body(meta):
    require(
        meta.get("status_code") == 200 and meta.get("body_captured") is True,
        "Inventory requires a captured successful response",
    )
    data = gzip.decompress(Path(meta["artifact"]).read_bytes())
    require(
        hashlib.sha256(data).hexdigest() == meta.get("body_sha256"),
        "Inventory capture hash changed",
    )
    return data


def verify_four_source(source, jobs, capture_paths):
    result = {
        "complete": False,
        "method": "csod_pages_v1" if source.ats_family == "csod" else "osce_full_search_v1",
        "scope": "configured public unfiltered board",
        "observed_count": len(jobs),
        "reasons": [],
        "capture_paths": [],
    }
    try:
        parsed = jobs_by_id(source, jobs)
        if source.ats_family == "csod":
            expected = dict(source.extra.get("search_payload") or {})
            size = int(source.extra.get("page_size", 25))
            expected.update(pageSize=size)
            expected.pop("pageNumber", None)
            require(
                all(
                    expected.get(key) in (None, "", [], {})
                    for key in (
                        "searchText",
                        "states",
                        "countryCodes",
                        "cities",
                        "placeID",
                        "radius",
                        "postingsWithinDays",
                        "customFieldCheckboxKeys",
                        "customFieldDropdowns",
                        "customFieldRadios",
                    )
                ),
                "World Bank census requires unfiltered Anytime search",
            )
            pages, total, seen = {}, None, set()
            for path in capture_paths:
                meta = json.loads(Path(path).read_text())
                if meta.get("url") != source.extra.get("api_url") or meta.get("method") != "POST":
                    continue
                require(
                    meta.get("response_url") == source.extra["api_url"], "CSOD endpoint redirected"
                )
                request = dict(meta.get("public_pagination_request") or {})
                require(
                    hashlib.sha256(json.dumps(request, separators=(",", ":")).encode()).hexdigest()
                    == meta.get("request_body_sha256"),
                    "CSOD captured request body hash differs",
                )
                number = request.pop("pageNumber", None)
                require(
                    request == expected and type(number) is int and number >= 1,
                    "CSOD request scope/page differs",
                )
                require(number not in pages, "Repeated CSOD captured page")
                value = json.loads(body(meta))
                data = value.get("data", value)
                count = data.get("totalCount")
                require(
                    type(count) is int and count >= 0 and (total is None or count == total),
                    "CSOD total missing/changed",
                )
                total = count
                rows = data.get("requisitions", data.get("jobs", data.get("items")))
                require(isinstance(rows, list), "CSOD rows missing")
                pages[number] = rows
                result["capture_paths"].append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                    }
                )
            require(
                total is not None
                and sorted(pages) == list(range(1, max(1, (total + size - 1) // size) + 1)),
                "Missing CSOD pages",
            )
            for number, rows in sorted(pages.items()):
                require(
                    len(rows) == min(size, max(0, total - (number - 1) * size)),
                    "CSOD page cardinality differs",
                )
                for row in rows:
                    key = next(
                        (
                            str(row[k])
                            for k in ("requisitionId", "id", "jobId")
                            if row.get(k) is not None
                        ),
                        None,
                    )
                    require(
                        key and key not in seen and key in parsed, "CSOD identity missing/repeated"
                    )
                    require(
                        raw_contains(parsed[key].raw, row),
                        "CSOD parsed row differs from captured row",
                    )
                    seen.add(key)
            require(seen == set(parsed) and len(seen) == total, "CSOD ID union differs")
        else:
            from jobagg.adapters.osce_inventory import SEARCH_URL, parse_bundle

            require(
                source.extra.get("listing_url", source.base_url) == SEARCH_URL,
                "OSCE latest feed is not full search",
            )
            roots = {Path(path).parent.parent for path in capture_paths}
            receipts = [p for root in roots for p in root.glob("browser-*/receipt.json")]
            require(len(receipts) == 1, "OSCE requires exactly one browser session receipt")
            receipt_path = receipts[0]
            receipt = json.loads(receipt_path.read_text())
            require(
                receipt["url"] == SEARCH_URL
                and receipt["contract"].get("inventory") == "osce_full_search_v1",
                "OSCE browser scope differs",
            )
            html = Path(receipt["html_path"]).read_bytes()
            require(
                hashlib.sha256(html).hexdigest() == receipt["html_sha256"],
                "OSCE browser evidence changed",
            )
            data_captures = []
            if source.extra.get("browser_render", {}).get("data_route") == "osce_job_results_v1":
                from jobagg.osce_fragments import verify_captures
                require(receipt["contract"].get("data_route") == "osce_job_results_v1",
                        "OSCE data-route receipt differs")
                require(receipt["contract"].get("csrf") == "osce_tss_token_v1",
                        "OSCE CSRF receipt differs")
                data_captures = verify_captures(html.decode(), capture_paths)
            captured, total, _ = parse_bundle(source, html.decode())
            require(
                {j.external_id for j in captured} == set(parsed),
                "OSCE parsed and captured ID sets differ",
            )
            for job in captured:
                require(
                    parsed[job.external_id].source_url == job.source_url
                    and parsed[job.external_id].title == job.title,
                    "OSCE card data differs",
                )
            result["capture_paths"] = [
                {
                    "path": str(receipt_path),
                    "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                }
            ] + data_captures
        result.update(
            complete=True,
            total_reported_by_source=total,
            reported_total=total,
            verified_zero=total == 0,
        )
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["reasons"].append(str(exc))
    return result


def verify_table_listing(source, jobs, capture_paths):
    result = {
        "complete": False,
        "method": source.id + "_tables_v1",
        "scope": "captured public category tables",
        "observed_count": len(jobs),
        "reasons": [],
        "capture_paths": [],
    }
    try:
        parsed = jobs_by_id(source, jobs)
        candidates = []
        endpoint = str(source.extra.get("listing_url") or source.base_url).rstrip("/")
        for path in capture_paths:
            meta = json.loads(Path(path).read_text())
            if meta.get("method") == "GET" and str(meta.get("url", "")).rstrip("/") == endpoint:
                require(
                    str(meta.get("response_url", "")).rstrip("/") == endpoint,
                    "Table listing redirected outside source",
                )
                candidates.append((path, body(meta).decode()))
        require(len(candidates) == 1, "Exactly one captured category board required")
        path, text = candidates[0]
        if source.id == "icddrb_custom_html":
            from jobagg.adapters.icddrb_inventory import all_board_links
            from jobagg.adapters.icddrb import _external_id

            links = all_board_links(text, source.base_url)
            require(links is not None, "icddr,b selected All board not established")
            captured = {_external_id(link["href"]): link for link in links}
            require(set(captured) == set(parsed), "icddr,b row IDs differ")
            for key, link in captured.items():
                require(
                    parsed[key].title == link["title"] and parsed[key].source_url == link["href"],
                    "icddr,b parsed card differs",
                )
        else:
            from jobagg.adapters.itcilo_public import parse_itcilo_board

            captured, evidence = parse_itcilo_board(source, text, endpoint)
            require(
                {j.external_id for j in captured} == set(parsed), "ITCILO category row IDs differ"
            )
            for job in captured:
                actual = parsed[job.external_id]
                require(
                    actual.title == job.title
                    and actual.source_url == job.source_url
                    and actual.raw.get("listing_cells") == job.raw["listing_cells"],
                    "ITCILO category row differs",
                )
            result["category_evidence"] = evidence
        result.update(
            complete=True,
            reported_total=len(parsed),
            verified_zero=not parsed,
            page_count=1,
            capture_paths=[
                {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            ],
        )
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["reasons"].append(str(exc))
    return result
