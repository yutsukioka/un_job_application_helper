"""Bind ICC/AfDB public HTML notices instead of their unresolved XML templates."""
from __future__ import annotations

from datetime import datetime
import hashlib
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urlsplit
import unicodedata

from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, clean_text
from jobagg.adapters.legacy_public_header import public_header

_SOURCES = {
    "icc_successfactors_legacy": ("career5.successfactors.eu", "1657261P"),
    "afdb_successfactors_legacy": ("career2.successfactors.eu", "africandev"),
}
_POSTING = re.compile(r'<div\b(?=[^>]*class=["\'][^"\']*\bexternalPosting\b[^"\']*["\'])[^>]*>', re.I)


class _PublicText(HTMLParser):
    """Keep adjacent inline text runs adjacent, as the public page renders them."""
    blocks = {"p", "div", "section", "article", "table", "tr", "td", "th", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0
        self.feed(body)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        if not self.ignored and tag in self.blocks:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored and tag in self.blocks:
            self.parts.append(" ")

    def handle_data(self, value):
        if not self.ignored:
            self.parts.append(value)

    def text(self):
        return unicodedata.normalize("NFC", " ".join("".join(self.parts).split()))


def _text(value):
    return clean_text(value) or ""


def public_body(html_text: str) -> str:
    openings = list(_POSTING.finditer(html_text))
    if len(openings) != 1:
        raise ValueError("One public externalPosting container required")
    opening = openings[0]
    depth = 1
    for tag in re.finditer(r"</?div\b[^>]*>", html_text[opening.end():], re.I):
        depth += -1 if tag[0].startswith("</") else 1
        if depth == 0:
            result = html_text[opening.end():opening.end() + tag.start()]
            if len(_text(result)) < 300:
                raise ValueError("Public legacy notice body is incomplete")
            return result
    raise ValueError("Public legacy posting container is not closed")


class _FirstTable(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.started = False
        self.finished = False
        self.parts = None
        self.cells = []
        self.rows = []
        self.table_depth = 0
        self.wrapper_context = None
        self.nested_rows = None
        self.outer_text = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if self.finished:
            return
        if tag == "table":
            if self.started:
                # ICC24231 places one metadata table inside a single empty
                # presentation cell. Accept only that exact unambiguous shape.
                if (self.table_depth != 1 or self.wrapper_context is not None
                        or self.rows or self.cells or self.parts is None
                        or "".join(self.outer_text).strip()):
                    raise ValueError("Nested metadata tables require review")
                self.wrapper_context = (self.parts, self.cells, self.rows)
                self.parts, self.cells, self.rows = None, [], []
                self.table_depth = 2
            else:
                self.started, self.table_depth = True, 1
        if not self.started:
            return
        if tag == "tr":
            self.cells = []
        if tag in {"td", "th"}:
            self.parts = []
        if tag == "br" and self.parts is not None:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.started and not self.finished and self.table_depth == 1:
            self.outer_text.append(data)
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if not self.started or self.finished:
            return
        if tag in {"td", "th"} and self.parts is not None:
            self.cells.append(" ".join("".join(self.parts).split()))
            self.parts = None
        if tag == "tr":
            self.rows.append(self.cells)
        if tag == "table":
            if self.table_depth == 2:
                if self.parts is not None or not self.rows:
                    raise ValueError("Nested metadata table is incomplete")
                self.nested_rows = self.rows
                self.parts, self.cells, self.rows = self.wrapper_context
                self.table_depth = 1
                return
            if self.wrapper_context is not None:
                if self.rows != [[""]] or "".join(self.outer_text).strip():
                    raise ValueError("Metadata presentation wrapper has additional cells or text")
                self.rows = self.nested_rows
            self.table_depth = 0
            self.finished = True


def _icc_fields(body):
    table = _FirstTable(body)
    if not table.finished:
        raise ValueError("ICC public metadata table missing or incomplete")
    values = {}
    for cells in table.rows:
        if not cells or not cells[0].strip():
            continue
        label = cells[0].strip().rstrip(":").strip()
        content = [value for value in cells[1:] if value]
        if len(content) > 1 or label in values:
            raise ValueError("ICC metadata row has ambiguous values")
        values[label] = content[0] if content else ""
    if ("Duty Station" not in values or "Contract Duration" not in values
            or not {"Organizational Unit", "Organisational Unit"}.intersection(values)):
        raise ValueError("ICC required public metadata labels missing")
    return values


def _afdb_fields(body):
    listing = re.search(r"<ul\b[^>]*>(.*?)</ul>", body, re.I | re.S)
    if not listing:
        raise ValueError("AfDB public header missing")
    rows = [_PublicText(value).text() for value in re.findall(r"<li\b[^>]*>(.*?)</li>", listing[1], re.I | re.S)]
    values = {}
    for row in rows:
        match = re.fullmatch(r"(Grade|Position No\s*\.|Posting Date|Closing Date)\s*:\s*(.*)", row)
        if not match or match[1] in values:
            raise ValueError("AfDB public metadata labels malformed")
        values[match[1]] = match[2]
    if not {"Grade", "Posting Date", "Closing Date"}.issubset(values):
        raise ValueError("AfDB required public metadata labels missing")
    return values


def _known(value):
    if not value or re.search(r"\[\[[^]]+\]\]", value):
        return None
    return value


def _calendar(value, *, afdb=False):
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value or "").strip()
    if afdb:
        formats = ("%m/%d/%Y",)
    else:
        formats = ("%d %B %Y", "%Y-%m-%d")
        # Do not resolve an ambiguous numerical date merely from a URL locale.
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
        if match and int(match[1]) > 12:
            formats += ("%d/%m/%Y",)
        elif match and int(match[2]) > 12:
            formats += ("%m/%d/%Y",)
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def render_public_notice(source: OrganizationSource, html_text: str, *, page_url: str,
                         external_id: str, expected_title: str) -> JobRecord:
    if source.id not in _SOURCES:
        raise ValueError("Unsupported public legacy source")
    host, company = _SOURCES[source.id]
    url = urlsplit(page_url)
    query = parse_qs(url.query, keep_blank_values=True)
    if (url.scheme != "https" or url.hostname != host or url.path != "/career"
            or url.username or url.password or url.port not in (None, 443) or url.fragment
            or query.get("company") != [company]
            or query.get("career_ns") != ["job_listing"]
            or query.get("career_job_req_id") != [str(external_id)]):
        raise ValueError("Public legacy URL/source/requisition identity differs")
    titles = re.findall(r"<h1\b[^>]*>(.*?)</h1>", html_text, re.I | re.S)
    expected = "Career Opportunities: " + expected_title + " (" + str(external_id) + ")"
    if len(titles) != 1 or _text(titles[0]).casefold() != _text(expected).casefold():
        raise ValueError("Public legacy heading does not match the listing")
    title = _text(titles[0])[len("Career Opportunities: "):].rsplit(" (", 1)[0]
    body = public_body(html_text)
    afdb = source.id == "afdb_successfactors_legacy"
    values = _afdb_fields(body) if afdb else _icc_fields(body)
    header = public_header(html_text, source.id, external_id)
    employment_type = _known(values.get("Type of Appointment")) or header["unambiguous_program_type"]
    deadline = values.get("Closing Date" if afdb else "Deadline for Applications")
    calendar = _calendar(deadline, afdb=afdb)
    timezone = "Europe/Amsterdam" if re.search(r"midnight the hague time", deadline or "", re.I) else None
    resolution = {
        "record_kind": "detail", "provider": "successfactors_legacy_public_page",
        "source_id": source.id, "company": company, "external_id": str(external_id),
        "public_url": page_url, "public_fields": values,
        "public_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "public_posting_date": values.get("Posting Date"),
        "public_deadline": deadline, "public_deadline_calendar_date": calendar,
        "calendar_interpretation": "AfDB displayed US month/day/year dates" if afdb else "Month-name/ISO or unambiguous numerical date only",
        "utc_resolved": False, "posting_time_resolved": False,
        "deadline_reason": "Public calendar date lacks an explicit UTC cutoff; midnight may have an ambiguous day boundary",
        "public_header": header,
        "employment_type_basis": "Type of Appointment" if _known(values.get("Type of Appointment")) else (
            "Public header unambiguous program category" if header["unambiguous_program_type"] else None),
    }
    job = build_job(source, title=title, external_id=external_id, apply_url=page_url, source_url=page_url,
                    location=_known(values.get("Duty Station")),
                    department=_known(values.get("Organizational Unit") or values.get("Organisational Unit")),
                    employment_type=employment_type, description=body,
                    raw={"parser": "successfactors_legacy_public", "detail_html": html_text,
                         "detail_url": page_url, "legacy_public_notice_html": body,
                         "_legacy_public_field_resolution": resolution,
                         "_legacy_public_header": header,
                         "public_job_field": header["job_field"],
                         "public_job_category": header["category"],
                         "grade": _known(values.get("Grade")), "contract_type": _known(values.get("Type of Appointment"))})
    job.closes_at_local = calendar
    job.closes_tz = timezone
    # This is already parsed public text; do not reinterpret escaped literal
    # prose as HTML or insert spaces at inline formatting boundaries.
    job.description = _PublicText(body).text()
    return job
