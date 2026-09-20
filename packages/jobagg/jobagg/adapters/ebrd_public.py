"""EBRD's labelled public header with calendar-only deadline precision."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlsplit

from jobagg.models import JobRecord

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _plain(value):
    return " ".join(value.split())


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return "".join(c.text() if isinstance(c, _Node) else c for c in self.children)

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()


class _HTML(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.feed(body)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def public_fields(html_text: str) -> dict[str, Any]:
    nodes = list(_HTML(html_text).root.nodes())
    descriptions = [n for n in nodes if n.attrs.get("itemprop") == "description"]
    titles = [n for n in nodes if n.attrs.get("itemprop") == "title"]
    canonicals = [n.attrs.get("href") for n in nodes if n.tag == "link" and n.attrs.get("rel") == "canonical"]
    if len(descriptions) != 1 or len(titles) != 1 or len(canonicals) != 1:
        raise ValueError("EBRD public notice identity/body structure is incomplete")
    tables = []
    for table in descriptions[0].nodes():
        if table.tag != "table":
            continue
        values = {}
        for row in table.nodes():
            if row.tag != "tr":
                continue
            cells = [n for n in row.children if isinstance(n, _Node) and n.tag in {"td", "th"}]
            if len(cells) != 2:
                continue
            label, value = (_plain(n.text()) for n in cells)
            if label in values:
                raise ValueError("EBRD public table has a repeated label")
            values[label] = value
        if "Requisition ID" in values:
            tables.append(values)
    required = {"Requisition ID", "Office Country", "Office City", "Division", "Contract Type", "Posting End Date"}
    if len(tables) != 1 or not required <= tables[0].keys() or not tables[0]["Requisition ID"].isdigit():
        raise ValueError("EBRD labelled public metadata table is missing or ambiguous")
    public = tables[0]
    public_locations = [_plain(n.text()) for n in nodes if "jobGeoLocation" in (n.attrs.get("class") or "").split()]
    public_posting_dates = [_plain(n.text()) for n in nodes if n.tag == "p" and n.attrs.get("id") == "job-date"]
    companies = [_plain(n.text()) for n in nodes if n.tag == "p" and n.attrs.get("id") == "job-company"]
    if len(public_locations) != 1 or len(public_posting_dates) != 1 or companies != ["Company: EBRD"]:
        raise ValueError("EBRD visible location/posting/company header is unresolved")
    claims = {}
    for node in nodes:
        if node.tag == "meta" and node.attrs.get("itemprop") in {"datePosted", "validThrough"}:
            key = node.attrs["itemprop"]
            if key in claims:
                raise ValueError("Repeated EBRD publisher date claim")
            claims[key] = node.attrs.get("content")
    local = None
    value = public["Posting End Date"]
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        try:
            local = datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        except ValueError:
            pass
    return {
        "record_kind": "detail", "provider": "ebrd_labelled_public_notice", "public_title": _plain(titles[0].text()),
        "canonical_url": canonicals[0], "public_fields": public, "public_location": public_locations[0],
        "public_posting_date_label": public_posting_dates[0], "publisher_date_claims": claims,
        "html_sha256": hashlib.sha256(html_text.encode()).hexdigest(),
        "department": public["Division"] or None, "public_contract_type": public["Contract Type"] or None,
        "closes_at_local": local, "utc_resolved": False, "posting_time_resolved": False,
        "deadline_unknown_reason": "public_calendar_date_without_clock_or_timezone" if local else "unrecognized_public_deadline_label",
        "posting_time_unknown_reason": "public_posting_calendar_date_without_clock_or_timezone",
        "publisher_claim_policy": "Exact machine UTC timestamps retained separately; not substituted for the visible calendar-only public dates",
    }


def apply_public_fields(job: JobRecord, html_text: str) -> JobRecord:
    if job.source_id != "ebrd_successfactors":
        return job
    resolution = public_fields(html_text)
    url = urlsplit(str(job.raw.get("detail_url") or job.source_url or ""))
    identity = re.fullmatch(r"/job/[^/]+/(\d+)", url.path.rstrip("/"))
    if (url.scheme != "https" or url.netloc != "jobs.ebrd.com" or url.query or url.fragment
            or identity is None or identity[1] != str(job.external_id)
            or resolution["canonical_url"] != url.geturl() or job.apply_url != url.geturl() or job.source_url != url.geturl()
            or job.title != resolution["public_title"]):
        raise ValueError("EBRD public fields require exact canonical URL, title and external identity")
    resolution.update(external_id=job.external_id, source_url=url.geturl(),
                      retained_description_sha256=hashlib.sha256((job.description or "").encode()).hexdigest())
    job.location = resolution["public_location"] or None
    job.department = resolution["department"]
    job.employment_type = resolution["public_contract_type"]
    job.posted_at = None
    job.closes_at = None
    job.closes_at_local = resolution["closes_at_local"]
    job.closes_tz = None
    job.raw = {**job.raw, "_ebrd_public_field_resolution": resolution, "detail_html": html_text,
               "detail_url": url.geturl(), "parser": "successfactors_detail", "company": "EBRD",
               "contract_type": job.employment_type, "requisition_id": resolution["public_fields"]["Requisition ID"]}
    return job
