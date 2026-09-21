"""Project CERN's public vacancy metadata from its labelled HTML sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jobagg.models import JobRecord


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)

    def text(self):
        return re.sub(r"\s+", " ", "".join(n.text() if isinstance(n, _Node) else n for n in self.children)).strip()

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()

    def has_class(self, name):
        return name in str(self.attrs.get("class") or "").split()


class _DOM(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _one(nodes, label, *, required=False):
    if len(nodes) > 1 or (required and not nodes):
        raise ValueError("CERN public metadata missing or ambiguous: " + label)
    return nodes[0] if nodes else None


def public_fields(html_text: str, page_url: str) -> dict[str, Any]:
    url = urlsplit(page_url)
    if (url.scheme != "https" or url.netloc != "careers.cern"
            or not re.fullmatch(r"/jobs/[^/]+/", url.path) or url.query or url.fragment):
        raise ValueError("CERN metadata requires its exact public notice URL")
    nodes = list(_DOM(html_text).root.nodes())
    canonical = _one([n for n in nodes if n.tag == "link" and n.attrs.get("rel") == "canonical"], "canonical URL", required=True)
    if canonical.attrs.get("href") != page_url:
        raise ValueError("CERN public canonical URL differs from the requested notice")
    heading = _one([n for n in nodes if n.tag == "h1" and n.has_class("job-offer__title")], "title", required=True)
    header = _one([n for n in nodes if n.has_class("job-offer__header-infos")], "header", required=True)
    header_nodes = list(header.nodes())
    values = {}
    for key, name in (("reference", "ref"), ("location", "location"), ("deadline", "before"), ("ideal_start_header", "date")):
        node = _one([n for n in header_nodes if n.has_class(name)], key, required=key == "reference")
        values[key] = node.text() if node else None
    conditions = [n for n in nodes if n.has_class("item") and n.has_class("has-icon")]
    fields = {}
    for item in conditions:
        labels = [n for n in item.children if isinstance(n, _Node) and n.tag == "strong"]
        bodies = [n for n in item.children if isinstance(n, _Node) and n.tag == "span"]
        if len(labels) == 1 and len(bodies) == 1:
            label = labels[0].text().rstrip(":")
            value = bodies[0].text()
            if label in fields and fields[label] != value:
                raise ValueError("CERN conflicting public condition: " + label)
            fields[label] = value
    department_node = _one([n for n in nodes if n.has_class("job-offer__department-content")], "department")
    department_text = None
    if department_node:
        text_node = _one([n for n in department_node.nodes() if n.has_class("text")], "department text", required=True)
        department_text = text_node.text()
    # Use only an explicitly named subject of the actual Department section.
    # Never expand a reference prefix into a guessed department name.
    named = re.match(r"(?:The )?([A-Z]{2,5}) (?:[Dd]epartment|sector|delivers)\b", department_text or "")
    resolution = {
        "record_kind": "detail", "provider": "cern_labelled_public_notice",
        "canonical_url": page_url, "external_id": url.path.split("/")[2],
        "public_title": heading.text(), "header_fields": values, "condition_fields": fields,
        "public_department_text": department_text, "department": named[1] if named else None,
        "public_contract_type": fields.get("Contract type"),
        "public_grade": fields.get("Grade range"),
        "posting_time_resolved": False, "utc_resolved": False,
        "closes_at": None, "closes_at_local": None, "closes_tz": None,
        "deadline_kind": "missing_or_unparsed_public_deadline",
    }
    deadline = values["deadline"] or ""
    match = re.fullmatch(r"Before (\d{2}/\d{2}/\d{4}) at (\d{2}:\d{2}) \(Geneva Time\)", deadline)
    if not match:
        date_only = re.fullmatch(r"Before (\d{2}/\d{2}/\d{4})", deadline)
        if date_only:
            try:
                resolution.update(closes_at_local=datetime.strptime(date_only[1], "%d/%m/%Y").date().isoformat(),
                                  deadline_kind="public_calendar_date_only")
            except ValueError:
                pass
        return resolution
    try:
        local = datetime.strptime(match[1] + " " + match[2], "%d/%m/%Y %H:%M")
    except ValueError:
        return resolution
    zone = ZoneInfo("Europe/Zurich")
    aware = local.replace(tzinfo=zone)
    resolution.update(closes_at_local=local.isoformat(timespec="minutes"), closes_tz="Europe/Zurich",
                      public_timezone_label="Geneva Time", timezone_mapping_basis="CERN public Geneva civil-time label")
    if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
        resolution["deadline_kind"] = "nonexistent_public_local_time"
    elif local.replace(tzinfo=zone, fold=1).utcoffset() != aware.utcoffset():
        resolution["deadline_kind"] = "ambiguous_public_local_time"
    else:
        resolution.update(deadline_kind="public_clock_and_named_civil_timezone", utc_resolved=True,
                          closes_at=aware.astimezone(UTC).isoformat())
    return resolution


def apply_public_fields(job: JobRecord, html_text: str, page_url: str) -> JobRecord:
    if job.source_id != "cern_custom_html":
        return job
    resolution = public_fields(html_text, page_url)
    if job.external_id != resolution["external_id"]:
        raise ValueError("CERN record identity differs from its exact public URL")
    job.title = resolution["public_title"]
    job.location = resolution["header_fields"]["location"]
    job.department = resolution["department"]
    job.employment_type = resolution["public_contract_type"]
    job.posted_at = None
    job.closes_at = datetime.fromisoformat(resolution["closes_at"]) if resolution["utc_resolved"] else None
    job.closes_at_local, job.closes_tz = resolution["closes_at_local"], resolution["closes_tz"]
    job.raw.update(detail_html=html_text, parser="static_detail", href=page_url,
                   grade=resolution["public_grade"], contract_type=resolution["public_contract_type"],
                   _cern_public_field_resolution=resolution)
    return job
