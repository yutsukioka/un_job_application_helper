"""IDB's public full-board DOM contract (not the empty legacy RSS feed).

The observed board supports pageNumber and sortBy=date. Each current walk is
bounded and checked against its visible range; only the two-sort identity union
may reconcile the current advertised count. This does not certify job details.
"""

from dataclasses import asdict
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from jobagg.models import JobRecord

_VERSION = "idb-fullboard-dom-v1"
_BASE = "https://jobs.iadb.org/go/All-Jobs/9638000/"
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def board_url(source, page, sort):
    configured = source.extra.get("public_all_jobs_url") or source.extra.get("all_jobs_url")
    if (
        configured != _BASE
        or type(page) is not int
        or page < 0
        or sort not in ("relevance", "date")
    ):
        raise ValueError("IDB full-board scope requires the exact configured public All Jobs route")
    query = {} if page == 0 and sort == "relevance" else {"pageNumber": str(page)}
    if sort == "date":
        query["sortBy"] = "date"
    return _BASE + ("?" + urlencode(query) if query else "")


class _VisibleBoard(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.text, self.anchors, self.controls = [], [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = bool(self.stack and self.stack[-1]["hidden"]) or tag in {
            "script",
            "style",
            "template",
            "noscript",
        }
        hidden |= "hidden" in attrs or bool(
            re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\b",
                attrs.get("style", ""),
                re.I,
            )
        )
        node = {"tag": tag, "attrs": attrs, "hidden": hidden, "parts": []}
        if tag not in _VOID:
            self.stack.append(node)
        if not hidden and tag in {
            "p",
            "div",
            "section",
            "li",
            "br",
            "h1",
            "h2",
            "h3",
            "button",
            "a",
        }:
            self.text.append(" ")

    def handle_endtag(self, tag):
        positions = [i for i, n in enumerate(self.stack) if n["tag"] == tag]
        if not positions:
            return
        index = positions[-1]
        closed = self.stack[index:]
        self.stack = self.stack[:index]
        for node in reversed(closed):
            if node["hidden"]:
                continue
            label = " ".join("".join(node["parts"]).split())
            if node["tag"] == "a" and node["attrs"].get("href"):
                self.anchors.append((node["attrs"]["href"], label))
            if node["tag"] in {"a", "button"}:
                self.controls.append((node["attrs"], label))
        self.text.append(" ")

    def handle_data(self, value):
        if self.stack and self.stack[-1]["hidden"]:
            return
        self.text.append(value)
        for node in self.stack:
            if not node["hidden"]:
                node["parts"].append(value)


def parse_board(html, page_url, *, expected_page):
    parsed = urlsplit(page_url)
    if (parsed.scheme, parsed.netloc, parsed.path) != (
        "https",
        "jobs.iadb.org",
        "/go/All-Jobs/9638000/",
    ):
        raise ValueError("IDB full-board response left the configured route")
    query = dict(parse_qsl(parsed.query))
    if set(query) - {"pageNumber", "sortBy"} or query.get("pageNumber", "0") != str(expected_page):
        raise ValueError("IDB full-board page query differs")
    reader = _VisibleBoard()
    reader.feed(html)
    visible = " ".join("".join(reader.text).split())
    ranges = set(
        re.findall(r"\b([0-9,]+)\s+to\s+([0-9,]+)\s+of\s+([0-9,]+)\s+results\b", visible, re.I)
    )
    if len(ranges) != 1:
        raise ValueError("IDB full-board rendered result range is missing or ambiguous")
    first, last, total = [int(x.replace(",", "")) for x in next(iter(ranges))]
    # Ten slots per page are the observed public widget contract. Unknown
    # layouts stay incomplete rather than deriving a page size from duplicates.
    if first != expected_page * 10 + 1 or not first <= last <= min(total, first + 9):
        raise ValueError("IDB full-board visible range did not advance exactly")
    rows = []
    for href, title in reader.anchors:
        url = urljoin(page_url, href)
        p = urlsplit(url)
        match = re.fullmatch(r"/job/[^/]+/(\d+)-en_US/?", p.path)
        if p.netloc == "jobs.iadb.org" and "/job/" in p.path and not match:
            raise ValueError("Unrecognized IDB public vacancy identity or locale")
        if not match:
            continue
        if p.scheme != "https" or p.netloc != "jobs.iadb.org" or p.query or p.fragment or not title:
            raise ValueError("IDB public vacancy link is outside the exact contract")
        rows.append({"external_id": match[1], "title": title, "url": url})
    if len(rows) != last - first + 1 or len({r["external_id"] for r in rows}) != len(rows):
        raise ValueError("IDB full-board visible job links do not match result slots")
    next_available = any(
        ("next" in attrs.get("aria-label", "").lower() or label.casefold() == "next")
        and "disabled" not in attrs
        and attrs.get("aria-disabled") != "true"
        for attrs, label in reader.controls
    )
    if next_available != (last < total):
        raise ValueError("IDB Next control disagrees with current result range")
    return {
        "version": _VERSION,
        "url": page_url,
        "page": expected_page,
        "first": first,
        "last": last,
        "total": total,
        "next_available": next_available,
        "rows": rows,
        "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def fetch_fullboard(adapter, rss_jobs):
    source = adapter.source
    cap = source.extra.get("max_pages", 5)
    if type(cap) is not int or not 1 <= cap <= 50:
        raise ValueError("IDB full-board page cap must be within 1–50 per sort")
    rows, totals, walks, pages = {}, set(), [], []
    diagnostics = adapter.run_diagnostics
    prior_rss = {"jobs": [asdict(j) for j in rss_jobs], "diagnostics": asdict(diagnostics)}
    reasons = []
    for sort in ("relevance", "date"):
        terminal = False
        walk_ids = []
        for page in range(cap):
            url = board_url(source, page, sort)
            adapter.ensure_allowed(url)
            response = adapter.context.http.get(url)
            if response.status_code != 200 or response.url != url:
                raise ValueError("IDB full-board request did not return its exact successful URL")
            try:
                proof = parse_board(response.text, url, expected_page=page)
            except ValueError as exc:
                reasons.append(str(exc))
                break
            proof["sort"] = sort
            pages.append(proof)
            totals.add(proof["total"])
            for row in proof["rows"]:
                existing = rows.get(row["external_id"])
                if existing and existing != row:
                    raise ValueError("IDB current walks disagree on vacancy title or URL")
                rows[row["external_id"]] = row
                walk_ids.append(row["external_id"])
            if len(totals) != 1:
                raise ValueError("IDB current advertised total changed during the walk")
            if not proof["next_available"]:
                terminal = True
                break
        walks.append(
            {
                "sort": sort,
                "terminal": terminal,
                "observed_rows": len(walk_ids),
                "unique_rows": len(set(walk_ids)),
            }
        )
        if reasons:
            break
        if not terminal:
            reasons.append(f"IDB {sort} full-board page cap reached")
            break
    complete = (
        len(walks) == 2
        and all(w["terminal"] for w in walks)
        and len(totals) == 1
        and len(rows) == next(iter(totals))
    )
    if not complete and not reasons:
        reasons.append("IDB both-sort union does not equal current advertised total")
    diagnostics.pages_fetched = len(pages)
    diagnostics.total_reported_by_source = next(iter(totals)) if len(totals) == 1 else None
    diagnostics.pagination_complete = complete
    diagnostics.list_error_count = 0 if complete else 1
    diagnostics.health_status = "ok" if complete else "issue"
    diagnostics.empty_reason = None if complete else "; ".join(reasons)
    diagnostics.zero_fetched_evidence = {
        "method": _VERSION,
        "legacy_rss": prior_rss,
        "public_widget_scope_verified": complete,
        "walks": walks,
        "pages": pages,
        "whole_public_detail_complete": False,
    }
    # RSS observations remain separate; they cannot silently expand or replace
    # the public board's current identity set.
    return [
        JobRecord(
            source_id=source.id,
            org_id=source.id,
            ats_family=source.ats_family,
            external_id=r["external_id"],
            title=r["title"],
            apply_url=r["url"],
            source_url=r["url"],
            raw={
                "external_id": r["external_id"],
                "title": r["title"],
                "href": r["url"],
                "parser": _VERSION,
            },
        )
        for r in rows.values()
    ]
