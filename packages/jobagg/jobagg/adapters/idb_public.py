"""Read IDB's visible notice labels without inferring missing public fields."""

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
_BREAKS = {"br", "p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6"}
_CONTRACT_HEADINGS = {"type of contract and duration", "tipo de contrato y duración",
                      "type de contrat et durée", "tipo de contrato e duração"}
_CONTRACT_LABELS = {"type of contract", "tipo de contrato", "type de contrat"}


def _plain(value: str) -> str:
    return " ".join(value.split())


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    parent: _Node | None = None

    def content(self, *, lines: bool = False) -> str:
        if self.tag in {"script", "style"}:
            return ""
        body = "".join(child.content(lines=lines) if isinstance(child, _Node) else child for child in self.children)
        return "\n" + body + "\n" if lines and self.tag in _BREAKS else body

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _HTML(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), parent=self.stack[-1])
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
                return

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def _deadline(label: str) -> dict[str, Any]:
    local = None
    normalized = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", label).strip().rstrip(".")
    has_est = normalized.endswith(" EST")
    if has_est:
        normalized = normalized[:-4]
    for pattern in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B, %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(normalized, pattern)
        except ValueError:
            continue
        local = parsed.isoformat(timespec="minutes") if "%I" in pattern else parsed.date().isoformat()
        break
    return {
        "public_label": label, "public_timezone_label": "EST" if has_est else None,
        "closes_at_local": local, "closes_at": None, "closes_tz": None, "utc_resolved": False,
        "unknown_reason": ("unrecognized_public_date" if local is None else
                           "EST_fixed_standard_versus_colloquial_Eastern_not_disambiguated" if has_est else
                           "public_calendar_date_without_clock_or_timezone"),
    }


def public_fields(html_text: str) -> dict[str, Any] | None:
    dom = _HTML(html_text)
    nodes = list(dom.root.walk())
    descriptions = [node for node in nodes if node.attrs.get("itemprop") == "description"]
    titles = [node for node in nodes if node.attrs.get("itemprop") == "title"]
    if len(descriptions) != 1 or len(titles) != 1:
        return None
    header = {}
    for node in nodes:
        if "joblayouttoken-label" not in (node.attrs.get("class") or "").split():
            continue
        label = _plain(node.content()).rstrip(":")
        siblings = node.parent.children if node.parent else []
        position = next(index for index, sibling in enumerate(siblings) if sibling is node)
        values = [child for child in siblings[position + 1:] if isinstance(child, _Node)]
        if (len(values) != 1 or values[0].tag != "span"
                or "rtltextaligneligible" not in (values[0].attrs.get("class") or "").split()):
            raise ValueError("Unrecognized IDB visible header label/value structure")
        value = _plain(values[0].content())
        if label in header and header[label] != value:
            raise ValueError("Conflicting IDB public header label")
        header[label] = value
    if not {"City", "Company", "Posting End Date"} <= header.keys():
        return None
    lines = [_plain(line) for line in descriptions[0].content(lines=True).splitlines() if _plain(line)]
    section_lines = []
    contract_heading = None
    for heading in descriptions[0].walk():
        if heading.tag != "h2" or _plain(heading.content()).lower().rstrip(":") not in _CONTRACT_HEADINGS:
            continue
        if contract_heading is not None:
            raise ValueError("Repeated IDB public contract section")
        contract_heading = _plain(heading.content())
        # The actual publisher template nests each H2 and its following content
        # in one section div. Bound that section instead of scanning later text.
        section = heading.parent.parent if heading.parent else None
        if section is None or len([node for node in section.walk() if node.tag == "h2"]) != 1:
            raise ValueError("Unrecognized IDB public contract section structure")
        section_lines = [_plain(line).lstrip("• ") for line in section.content(lines=True).splitlines() if _plain(line)]
        section_lines = [line for line in section_lines if line.lower().rstrip(": ") not in _CONTRACT_HEADINGS]
    contract_type, contract_basis = None, None
    for line in section_lines:
        if ":" in line and line.split(":", 1)[0].strip().lower() in _CONTRACT_LABELS:
            value = line.split(":", 1)[1].strip()
            if contract_type is not None and contract_type != value:
                raise ValueError("Conflicting IDB explicit contract types")
            contract_type, contract_basis = value or None, "explicit_type_label"
    if contract_type is None and section_lines:
        prefix = section_lines[0].split(",", 1)[0]
        # Preserve the source's own phrase (including misspellings and prefixes)
        # only when it is a short contract phrase before a duration clause.
        if ("," in section_lines[0] and len(prefix) <= 100
                and re.search(r"\b(?:consultant|consultor|staff contract)\b", prefix, re.I)):
            contract_type, contract_basis = prefix, "public_contract_duration_phrase_before_comma"
    return {
        "record_kind": "detail", "provider": "idb_labelled_public_notice",
        "public_title": _plain(titles[0].content()), "header_labels": header,
        "html_sha256": hashlib.sha256(html_text.encode()).hexdigest(),
        "public_description_text_sha256": hashlib.sha256(_plain(" ".join(lines)).encode()).hexdigest(),
        "contract_section_heading": contract_heading, "contract_section_lines": section_lines,
        "public_contract_type": contract_type, "contract_type_basis": contract_basis,
        "contract_type_unknown_reason": None if contract_type else ("conditional_or_unrecognized_public_terms" if section_lines else "no_explicit_contract_section"),
        "deadline": _deadline(header["Posting End Date"]),
        "department_unknown_reason": "no_explicit_department_label_company_is_separate",
        "posting_time_unknown_reason": "no_public_posting_date_label",
        "scope": "visible_header_and_contract_terms; full description retained; attachment verification separate",
    }


def apply_public_fields(job: JobRecord, html_text: str) -> JobRecord:
    if job.source_id != "idb_successfactors":
        return job
    resolution = public_fields(html_text)
    if resolution is None:
        return job
    url = urlsplit(str(job.raw.get("detail_url") or job.source_url or ""))
    identity = re.fullmatch(r"/job/[^/]+/(\d+)-[a-z]{2}_[A-Z]{2}", url.path.rstrip("/"))
    if (url.scheme != "https" or url.netloc != "jobs.iadb.org" or url.query or url.fragment
            or identity is None or identity[1] != str(job.external_id)
            or job.apply_url != url.geturl() or job.source_url != url.geturl()
            or job.title != resolution["public_title"]):
        raise ValueError("IDB public field metadata requires exact captured notice identity")
    resolution["external_id"] = job.external_id
    resolution["source_url"] = url.geturl()
    resolution["retained_description_sha256"] = hashlib.sha256((job.description or "").encode()).hexdigest()
    job.location = resolution["header_labels"]["City"] or None
    job.department = None
    job.employment_type = resolution["public_contract_type"]
    job.posted_at = None
    job.closes_at = None
    job.closes_at_local = resolution["deadline"]["closes_at_local"]
    job.closes_tz = None
    job.raw = {**job.raw, "detail_html": html_text, "detail_url": url.geturl(), "parser": "successfactors_detail",
               "company": resolution["header_labels"]["Company"], "contract_type": job.employment_type,
               "_idb_public_field_resolution": resolution}
    return job
