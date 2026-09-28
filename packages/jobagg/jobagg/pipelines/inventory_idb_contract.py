"""Recheck IDB page evidence; adapter diagnostics alone never certify a census."""

import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from jobagg.adapters.idb_inventory import board_url, parse_board


def verify_idb_listing(source, jobs, capture_paths):
    result = {
        "complete": False,
        "method": "idb_fullboard_dom_v1",
        "observed_count": len(jobs),
        "scope": "Current public All Jobs two-sort union, en_US widget locale",
        "reasons": [],
        "capture_paths": [],
        "browser_receipts": [],
    }
    try:
        roots = {Path(p).parent.parent.resolve() for p in capture_paths}
        if len(roots) != 1:
            raise ValueError("IDB census requires one captured listing task")
        root = next(iter(roots))
        page_bodies, original_urls, rendered_urls = {}, set(), set()
        for p in capture_paths:
            path = Path(p)
            meta = json.loads(path.read_text())
            url = meta.get("url", "")
            if urlsplit(url).path != "/go/All-Jobs/9638000/":
                continue
            if (
                meta.get("phase", {}).get("kind") != "listing"
                or meta.get("method") != "GET"
                or meta.get("status_code") != 200
                or meta.get("body_captured") is not True
                or meta.get("response_url") != url
            ):
                raise ValueError("IDB board has no exact successful original HTTP response")
            body = gzip.decompress(Path(meta["artifact"]).read_bytes())
            if hashlib.sha256(body).hexdigest() != meta.get("body_sha256"):
                raise ValueError("IDB original board bytes changed")
            if url in original_urls:
                raise ValueError("IDB page was requested more than once in the listing pass")
            original_urls.add(url)
            page_bodies[url] = body.decode("utf-8")
            result["capture_paths"].append(
                {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        for p in sorted(root.glob("browser-*/receipt.json")):
            receipt = json.loads(p.read_text())
            url = receipt.get("url")
            if url not in original_urls:
                continue
            path = Path(receipt["html_path"])
            if not path.resolve().is_relative_to(p.parent.resolve()):
                raise ValueError("IDB rendered page escapes its browser receipt")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != receipt.get("html_sha256"):
                raise ValueError("IDB rendered page bytes changed")
            if url in rendered_urls:
                raise ValueError("Duplicate IDB rendered page")
            rendered_urls.add(url)
            page_bodies[url] = data.decode("utf-8")
            result["browser_receipts"].append(
                {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            )
        pages = {}
        for url, body in page_bodies.items():
            query = dict(parse_qsl(urlsplit(url).query))
            page = int(query.get("pageNumber", "0"))
            sort = "date" if query.get("sortBy") == "date" else "relevance"
            if url != board_url(source, page, sort):
                raise ValueError("IDB request scope or sort differs from observed contract")
            pages[(sort, page)] = parse_board(body, url, expected_page=page)
        union, totals, expected_pages = {}, set(), set()
        for sort in ("relevance", "date"):
            terminal = False
            for page in range(source.extra.get("max_pages", 5)):
                proof = pages.get((sort, page))
                if proof is None:
                    raise ValueError("IDB current sort walk has a missing page")
                expected_pages.add((sort, page))
                totals.add(proof["total"])
                for row in proof["rows"]:
                    old = union.get(row["external_id"])
                    if old and old != row:
                        raise ValueError("IDB title or URL changed between current pages/sorts")
                    union[row["external_id"]] = row
                if not proof["next_available"]:
                    terminal = True
                    break
            if not terminal:
                raise ValueError("IDB census page cap reached")
        actual = {
            j.external_id: {"external_id": j.external_id, "title": j.title, "url": j.apply_url}
            for j in jobs
        }
        if any(
            j.source_url != j.apply_url
            or j.raw.get("href") != j.apply_url
            or j.raw.get("external_id") != j.external_id
            or j.raw.get("title") != j.title
            for j in jobs
        ):
            raise ValueError("IDB parsed dispatch fields differ from current public rows")
        if (
            expected_pages != set(pages)
            or len(totals) != 1
            or len(union) != next(iter(totals))
            or len(actual) != len(jobs)
            or actual != union
            or any(j.source_id != source.id for j in jobs)
        ):
            raise ValueError("IDB current union, parsed rows, or advertised count differs")
        result.update(
            complete=True,
            reported_total=next(iter(totals)),
            page_count=len(pages),
            identity_unit="public_requisition_id",
            verified_zero=False,
        )
    except (ValueError, TypeError, KeyError, OSError, EOFError) as exc:
        result["reasons"].append(str(exc))
    return result
