"""Avature portal adapter for public HTML job listings."""

from __future__ import annotations

import html
import hashlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html as _clean_html

_ARTICLE_RE = re.compile(
    r'<article[^>]+class="[^"]*\barticle--result\b[^"]*"[^>]*>(?P<html>.*?)(?=</article>)',
    re.IGNORECASE | re.DOTALL,
)
_DETAIL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*?/JobDetail/[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SUBTITLE_RE = re.compile(
    r'<div[^>]+class="[^"]*\barticle__header__text__subtitle\b[^"]*"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_SUMMARY_RE = re.compile(
    r'<div[^>]+class="[^"]*\barticle__content\b[^"]*"[^>]*>(?P<body>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
@register_adapter
class AvatureAdapter(JobAdapter):
    family = "avature"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        page_size = _as_int(self.source.extra.get("page_size"), default=25)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        for page in range(max_pages):
            page_url = _url_with_query(
                listing_url,
                {
                    "jobRecordsPerPage": page_size,
                    "jobOffset": page * page_size,
                },
            )
            page_jobs = self.parse_listing_html(self.fetch_text(page_url))
            if not page_jobs:
                break
            page_new = 0
            for job in page_jobs:
                if fetch_details:
                    detail_job = self.fetch_detail_for_listing_item(job.raw)
                    if detail_job is not None:
                        job = detail_job
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
                page_new += 1
            if page_new == 0 or len(page_jobs) < page_size:
                break
        return jobs

    def parse_listing_html(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for match in _ARTICLE_RE.finditer(html_text):
            item_html = match.group("html")
            link = _DETAIL_LINK_RE.search(item_html)
            if not link:
                continue
            detail_url = urljoin(self.source.base_url, html.unescape(link.group("href")))
            subtitle_parts = _subtitle_parts(item_html)
            jobs.append(
                build_job(
                    self.source,
                    title=_clean_html(link.group("title")),
                    external_id=_job_id_from_url(detail_url),
                    location=subtitle_parts[0] if subtitle_parts else None,
                    department=subtitle_parts[1] if len(subtitle_parts) > 1 and self.source.id != "unops_avature" else None,
                    posted_at=subtitle_parts[2] if len(subtitle_parts) > 2 and self.source.id != "unops_avature" else None,
                    apply_url=detail_url,
                    source_url=detail_url,
                    description=_summary(item_html),
                    raw={"listing_html": item_html, "_detail_url": detail_url,
                         "listing_date_text": subtitle_parts[2] if len(subtitle_parts) > 2 else None,
                         "listing_seniority_level": subtitle_parts[1] if len(subtitle_parts) > 1 else None},
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("_detail_url")
        if not detail_url:
            return None
        return self.parse_detail_html(self.fetch_text(str(detail_url)), str(detail_url))

    def parse_detail_html(self, html_text: str, detail_url: str) -> JobRecord:
        fields = _detail_fields(html_text)
        title = fields.get("Position Title") or _meta_content(html_text, "og:title") or _title_from_url(detail_url)
        apply_url = fields.get("Apply URL") or detail_url
        unops = self.source.id == "unops_avature"
        content_html = _main_content(html_text)
        competency_labels = []
        if unops:
            content_html, competency_labels = _unops_competency_content(content_html)
        job = build_job(
            self.source,
            title=title,
            external_id=_job_id_from_url(detail_url),
            location=fields.get("Duty Station(s)") or fields.get("Duty Station") or fields.get("Location"),
            department=(fields.get("Department") if unops else fields.get("Seniority Level") or fields.get("Level")),
            employment_type=fields.get("Contract type") or fields.get("Contract Type"),
            posted_at=None if unops else fields.get("Posted") or fields.get("Posting Start Date"),
            closes_at=None if unops else fields.get("Posting End Date"),
            apply_url=apply_url,
            source_url=detail_url,
            description=_clean_html(content_html),
            raw={"detail_html": html_text, "_detail_url": detail_url, "avature_fields": fields},
        )
        if unops:
            job.posted_at, posting_resolution = _unops_posting_time(fields)
            job.raw["_avature_posting_time_resolution"] = {
                "record_kind": "detail", "provider": "unops_labelled_public_notice",
                "source_id": self.source.id, "external_id": job.external_id, "detail_url": detail_url,
                "detail_html_sha256": hashlib.sha256(html_text.encode()).hexdigest(),
                **posting_resolution,
            }
            job.raw["_avature_competency_text_resolution"] = {
                "scope": "img.competency__icon within the public Competencies details section",
                "public_labels_in_order": competency_labels,
                "labels_inserted_at_original_image_positions": True,
            }
            # The page gives a calendar date and a midnight notice, whose day
            # boundary is not an exact instant. Preserve both without guessing.
            job.closes_at_local = fields.get("Posting End Date")
            notice = re.search(r"before midnight Copenhagen time\s*\(CET\)", job.description or "", re.I)
            job.closes_tz = "Europe/Copenhagen" if notice else None
            job.raw["_avature_field_resolution"] = {"record_kind": "detail", "department_observed": "Department" in fields}
            job.raw["_avature_deadline_resolution"] = {
                "record_kind": "detail", "utc_resolved": False,
                "public_value": job.closes_at_local,
                "public_timezone_notice": notice.group(0) if notice else None,
                "kind": "calendar_date_midnight_boundary_unresolved" if notice else "calendar_date_timezone_unverified",
            }
        return job


def _unops_posting_time(fields: dict[str, str]) -> tuple[datetime | None, dict[str, Any]]:
    """An explicit public calendar date never supplies a posting clock/zone."""
    claims = {key: fields[key] for key in ("Posted", "Posting Start Date") if fields.get(key)}
    values = set(claims.values())
    result = {"public_fields": claims, "utc_resolved": False, "posted_at": None, "calendar_date": None,
              "kind": "unparsed_public_posting_date" if values else "public_posting_date_absent"}
    if len(values) != 1:
        if values:
            result["kind"] = "conflicting_public_posting_labels"
        return None, result
    value = next(iter(values))
    for form in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(value, form).date()
        except ValueError:
            continue
        result.update(kind="public_calendar_date_only", calendar_date=day.isoformat())
        return None, result
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, result
    if instant.tzinfo is None:
        result["kind"] = "public_clock_timezone_unknown"
        return None, result
    instant = instant.astimezone(UTC)
    result.update(kind="explicit_public_offset", utc_resolved=True, posted_at=instant.isoformat())
    return instant, result


class _UNOPSCompetencyLabels(HTMLParser):
    """Locate only role competency icons, retaining their source positions."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.line_offsets = [0]
        for line in text.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.details = []
        self.replacements = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "details":
            self.details.append({"eligible": "article--details" in classes, "heading": [], "in_summary": False})
        if not self.details:
            return
        current = self.details[-1]
        if tag == "summary":
            current["in_summary"] = True
        if (tag == "img" and "competency__icon" in classes and current["eligible"]
                and not current["in_summary"] and _clean_html("".join(current["heading"])) == "Competencies"):
            label = _clean_html(attrs.get("alt") or "")
            if not label:
                raise ValueError("UNOPS public competency icon has no readable alternative label")
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            self.replacements.append((start, start + len(self.get_starttag_text()), label))

    def handle_data(self, data):
        if self.details and self.details[-1]["in_summary"]:
            self.details[-1]["heading"].append(data)

    def handle_endtag(self, tag):
        if self.details:
            if tag == "summary":
                self.details[-1]["in_summary"] = False
            elif tag == "details":
                self.details.pop()


def _unops_competency_content(content_html: str) -> tuple[str, list[str]]:
    parser = _UNOPSCompetencyLabels(content_html)
    rendered = content_html
    for start, end, label in reversed(parser.replacements):
        rendered = rendered[:start] + "<span>" + html.escape(label) + "</span>" + rendered[end:]
    return rendered, [label for _, _, label in parser.replacements]


def _url_with_query(url: str, params: dict[str, Any]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in params.items() if value is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _subtitle_parts(item_html: str) -> list[str]:
    match = _SUBTITLE_RE.search(item_html)
    if not match:
        return []
    text = _clean_html(match.group("body")) or ""
    return [part.strip() for part in text.split("•") if part.strip()]


def _summary(item_html: str) -> str | None:
    match = _SUMMARY_RE.search(item_html)
    return _clean_html(match.group("body")) if match else None


def _job_id_from_url(url: str) -> str | None:
    match = re.search(r"/JobDetail/[^/]+/(?P<job_id>\d+)", urlsplit(url).path)
    return match.group("job_id") if match else None


def _detail_fields(html_text: str) -> dict[str, str]:
    parser = _DetailFieldsParser()
    parser.feed(html_text)
    parser.close()
    return parser.fields


class _DetailFieldsParser(HTMLParser):
    """Read complete field divs; a regex cut off the value's closing tag."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.field_depth = None
        self.part_depth = None
        self.part = None
        self.parts = {}
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            if self.part and tag == "br":
                self.parts[self.part].append(" ")
            return
        self.depth += 1
        classes = dict(attrs).get("class", "").split()
        if "article__content__view__field" in classes:
            if self.field_depth is not None:
                raise ValueError("Nested Avature metadata field")
            self.field_depth, self.parts = self.depth, {}
        if self.field_depth is not None:
            for part in ("label", "value"):
                if "article__content__view__field__" + part in classes:
                    if part in self.parts:
                        raise ValueError("Repeated Avature metadata field component")
                    self.part, self.part_depth = part, self.depth
                    self.parts[part] = []

    def handle_data(self, data):
        if self.part:
            self.parts[self.part].append(data)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.part_depth == self.depth:
            self.part, self.part_depth = None, None
        if self.field_depth == self.depth:
            label = _clean_html("".join(self.parts.get("label", [])))
            value = _clean_html("".join(self.parts.get("value", [])))
            if label and value:
                if label in self.fields and self.fields[label] != value:
                    raise ValueError("Conflicting Avature metadata field: " + label)
                self.fields[label] = value
            self.field_depth = None
        self.depth = max(0, self.depth - 1)


def _meta_content(html_text: str, property_name: str) -> str | None:
    match = re.search(
        rf'<meta[^>]+property="{re.escape(property_name)}"[^>]+content="(?P<value>[^"]+)"',
        html_text,
        re.IGNORECASE,
    )
    return html.unescape(match.group("value")) if match else None


def _main_content(html_text: str) -> str:
    match = re.search(
        r'<div[^>]+class="[^"]*\barticle__content__view\b[^"]*"[^>]*>(?P<body>.*?)</main>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group("body") if match else html_text


def _title_from_url(url: str) -> str:
    parts = [part for part in urlsplit(url).path.rstrip("/").split("/") if part]
    if len(parts) >= 2:
        return parts[-2].replace("-", " ")
    return "Untitled role"
