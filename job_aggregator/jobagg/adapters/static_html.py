"""Configurable parser for public static/custom HTML vacancy pages."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, canonical_url, clean_text
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html as _clean_html


@register_adapter
class StaticHTMLAdapter(JobAdapter):
    family = "static_html"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        parser_name = str(self.source.extra.get("parser") or self.source.ats_family)
        html_text = self.fetch_text(listing_url)
        blocked_reason = _blocked_page_reason(html_text)
        if blocked_reason:
            self.run_diagnostics.health_status = "issue"
            self.run_diagnostics.empty_reason = "blocked"
            raise RuntimeError(f"{self.source.id}: listing page blocked by {blocked_reason}")
        if parser_name == "unssc_drupal":
            return parse_unssc_jobs(self.source, html_text, listing_url)
        if parser_name in {"generic", "public_links"}:
            return self._parse_generic_links(html_text, listing_url)
        return self._parse_generic_links(html_text, listing_url)

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = (
            item.get("href")
            or item.get("document_url")
            or item.get("source_url")
            or item.get("url")
            or item.get("apply_url")
        )
        if not detail_url:
            return None
        detail_url = str(detail_url)
        self.ensure_allowed(detail_url)
        detail_job = parse_detail_page(self.source, self.fetch_text(detail_url), detail_url)
        listing_external_id = item.get("external_id") or item.get("code")
        if listing_external_id in (None, ""):
            return detail_job
        return replace(
            detail_job,
            external_id=str(listing_external_id),
            raw={
                **detail_job.raw,
                "listing_raw": item,
                "detail_url": detail_url,
            },
        )

    def _parse_generic_links(self, html_text: str, listing_url: str) -> list[JobRecord]:
        json_ld_jobs = parse_json_ld_jobs(self.source, html_text, listing_url)
        if json_ld_jobs:
            return json_ld_jobs

        detail_jobs = []
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=True)
        max_detail_jobs = _as_int(self.source.extra.get("max_detail_jobs"), default=100)
        seen: set[str] = set()
        links = _job_links_from_html(self.source, html_text, listing_url)
        if not links:
            structural_evidence = _verified_structural_empty_evidence(self.source, html_text)
            if structural_evidence.get("verified"):
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_structural_empty"
                self.run_diagnostics.zero_fetched_evidence = structural_evidence
                self.run_diagnostics.pagination_complete = True
                return []
            if _has_structural_empty_policy(self.source):
                self.run_diagnostics.health_status = "issue"
                self.run_diagnostics.empty_reason = "parser_no_match"
                self.run_diagnostics.zero_fetched_evidence = structural_evidence
                raise RuntimeError(
                    f"{self.source.id}: no job links found and structural empty markers were not verified"
                )
            empty_reason = _empty_board_reason(html_text)
            if empty_reason:
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_text_empty"
                self.run_diagnostics.zero_fetched_evidence = {"matched_text": empty_reason}
                self.run_diagnostics.pagination_complete = True
                return []
            raise RuntimeError(
                f"{self.source.id}: no job links found; selectors may be stale or page blocked"
            )
        for link in links:
            key = link["href"]
            if key in seen:
                continue
            seen.add(key)
            if fetch_details and len(detail_jobs) < max_detail_jobs:
                try:
                    detail_html = self.fetch_text(link["href"])
                    detail_job = parse_detail_page(self.source, detail_html, link["href"])
                    detail_jobs.append(detail_job)
                    continue
                except Exception:
                    pass
            detail_jobs.append(
                build_job(
                    self.source,
                    title=link["title"],
                    external_id=_external_id_from_url(link["href"]),
                    apply_url=link["href"],
                    source_url=link["href"],
                    raw={
                        "href": link["href"],
                        "external_id": _external_id_from_url(link["href"]),
                        "title": link["title"],
                        "parser": "public_links",
                    },
                )
            )
        return _dedupe(detail_jobs)


def parse_unssc_jobs(
    source: OrganizationSource,
    html_text: str,
    listing_url: str,
) -> list[JobRecord]:
    jobs = []
    for row in _table_rows(html_text):
        code = _cell_text(row, "views-field-field-vacancy-code-1")
        title_cell = _cell_html(row, "views-field-title")
        title, document_url = _first_anchor(title_cell, listing_url)
        if not code or not title:
            continue
        apply_cell = _cell_html(row, "views-field-nid")
        _, apply_url = _first_anchor(apply_cell, listing_url)
        posted_at = _time_datetime(_cell_html(row, "views-field-field-issue-date"))
        closes_at = _time_datetime(_cell_html(row, "views-field-field-application-deadline"))
        jobs.append(
            build_job(
                source,
                title=title,
                external_id=code,
                posted_at=posted_at,
                closes_at=closes_at,
                apply_url=apply_url or document_url or listing_url,
                source_url=document_url or listing_url,
                raw={
                    "code": code,
                    "external_id": code,
                    "document_url": document_url,
                    "apply_url": apply_url,
                    "parser": "unssc_drupal",
                },
            )
        )
    return jobs


def parse_json_ld_jobs(
    source: OrganizationSource,
    html_text: str,
    page_url: str,
) -> list[JobRecord]:
    jobs = []
    for payload in _json_ld_payloads(html_text):
        for item in _find_job_postings(payload):
            external_id = _identifier_value(item.get("identifier")) or _external_id_from_url(page_url)
            apply_url = item.get("url") or page_url
            jobs.append(
                build_job(
                    source,
                    title=item.get("title") or _title_from_html(html_text),
                    external_id=external_id,
                    location=_json_ld_location(item.get("jobLocation")),
                    department=_organization_name(item.get("hiringOrganization")),
                    employment_type=item.get("employmentType"),
                    posted_at=item.get("datePosted"),
                    closes_at=item.get("validThrough") or _deadline_from_text(item.get("description")),
                    apply_url=str(apply_url),
                    source_url=page_url,
                    description=item.get("description"),
                    raw={**item, "parser": "json_ld"},
                )
            )
    return _dedupe(jobs)


def parse_detail_page(
    source: OrganizationSource,
    html_text: str,
    page_url: str,
) -> JobRecord:
    json_ld_jobs = parse_json_ld_jobs(source, html_text, page_url)
    if json_ld_jobs:
        return json_ld_jobs[0]

    parsed = _TokenParser.parse(html_text)
    tokens = [token["text"] for token in parsed.tokens if token["type"] == "text"]
    title = (
        parsed.meta.get("og:title")
        or parsed.meta.get("twitter:title")
        or parsed.title
        or _title_from_html(html_text)
    )
    title = _strip_site_suffix(title)
    closes_at = _field_after(tokens, ("closing date", "deadline", "deadline for application", "application deadline"))
    location = _field_after(tokens, ("location", "duty station", "job location"))
    grade = _field_after(tokens, ("grade", "post level"))
    contract_type = _field_after(tokens, ("contract type", "contract", "type"))
    posted_at = _field_after(tokens, ("posted date", "date posted", "publication date", "posted on"))
    description = _clean_html(_mainish_html(html_text))
    return build_job(
        source,
        title=title,
        external_id=_external_id_from_url(page_url),
        location=location,
        employment_type=" / ".join(part for part in (grade, contract_type) if part) or None,
        posted_at=posted_at,
        closes_at=closes_at or _deadline_from_text(description),
        apply_url=page_url,
        source_url=page_url,
        description=description,
        raw={
            "grade": grade,
            "contract_type": contract_type,
            "parser": "static_detail",
            "href": page_url,
        },
    )


def _job_links_from_html(
    source: OrganizationSource,
    html_text: str,
    listing_url: str,
) -> list[dict[str, str]]:
    hint = str(source.extra.get("job_link_selector_hint") or "").lower()
    include_hints = source.extra.get("job_link_hints") or []
    if isinstance(include_hints, str):
        include_hints = [include_hints]
    if hint:
        include_hints = [hint, *include_hints]
    include_hints = [str(item).lower() for item in include_hints]

    exclude_hints = source.extra.get("exclude_link_selector_hint") or []
    if isinstance(exclude_hints, str):
        exclude_hints = [exclude_hints]
    exclude_hints = [str(item).lower() for item in exclude_hints]

    links = []
    for anchor in _TokenParser.parse(html_text).anchors:
        href = urljoin(listing_url, anchor["href"])
        title = _clean(anchor["text"])
        if not title or _is_generic_link_text(title):
            continue
        lowered = href.lower()
        if include_hints and not any(item in lowered for item in include_hints):
            continue
        if exclude_hints and any(item in lowered for item in exclude_hints):
            continue
        if canonical_url(href) == canonical_url(listing_url):
            continue
        links.append({"href": href, "title": title})
    return links


def _blocked_page_reason(html_text: str) -> str | None:
    lowered = html_text.casefold()
    if "attention required! | cloudflare" in lowered or "sorry, you have been blocked" in lowered:
        return "Cloudflare"
    return None


def _empty_board_reason(html_text: str) -> str | None:
    lowered = _clean(html_text).casefold()
    empty_markers = (
        "there are no vacancies available",
        "there are no vacancies",
        "there are no internships available",
        "there are no internships",
        "no current vacancies",
        "no vacancies available",
        "no vacancies at this time",
        "no jobs available",
        "no open positions",
    )
    for marker in empty_markers:
        if marker in lowered:
            return marker
    return None


def _has_structural_empty_policy(source: OrganizationSource) -> bool:
    policy = source.extra.get("empty_policy")
    return isinstance(policy, dict) and policy.get("mode") == "verified_structural_empty"


def _verified_structural_empty_evidence(
    source: OrganizationSource,
    html_text: str,
) -> dict[str, Any]:
    policy = source.extra.get("empty_policy")
    if not isinstance(policy, dict) or policy.get("mode") != "verified_structural_empty":
        return {}
    text = _clean(html_text)
    lowered = text.casefold()
    required = [str(marker) for marker in policy.get("required_page_markers") or []]
    missing_markers = [marker for marker in required if marker.casefold() not in lowered]
    section_start = str(policy.get("section_start_text") or "")
    section_start_found = not section_start or section_start.casefold() in lowered
    section_end_any = [str(marker) for marker in policy.get("section_end_any_text") or []]
    section_end_found = not section_end_any or any(marker.casefold() in lowered for marker in section_end_any)
    link_patterns = [str(pattern).casefold() for pattern in policy.get("job_link_patterns") or []]
    ignored_text = {str(value).casefold() for value in policy.get("ignore_link_text") or []}
    job_nodes = []
    for anchor in _TokenParser.parse(html_text).anchors:
        title = _clean(anchor["text"])
        href = str(anchor["href"])
        if title.casefold() in ignored_text:
            continue
        if link_patterns and not any(pattern in href.casefold() for pattern in link_patterns):
            continue
        if title:
            job_nodes.append({"title": title, "href": href})
    verified = not missing_markers and section_start_found and section_end_found and not job_nodes
    return {
        "verified": verified,
        "required_markers_found": not missing_markers,
        "missing_markers": missing_markers,
        "section_start_found": section_start_found,
        "section_end_found": section_end_found,
        "job_nodes_found": len(job_nodes),
        "job_nodes_sample": job_nodes[:5],
    }


def _table_rows(html_text: str) -> list[str]:
    return re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_text, flags=re.IGNORECASE | re.DOTALL)


def _cell_html(row_html: str, class_name: str) -> str:
    pattern = re.compile(
        rf"<td\b(?=[^>]*\bclass=[\"'][^\"']*\b{re.escape(class_name)}\b)[^>]*>(?P<body>.*?)</td>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(row_html)
    return match.group("body") if match else ""


def _cell_text(row_html: str, class_name: str) -> str | None:
    return clean_text(_cell_html(row_html, class_name))


def _first_anchor(html_text: str, base_url: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    return clean_text(match.group("title")), urljoin(base_url, html.unescape(match.group("href")))


def _time_datetime(html_text: str) -> str | None:
    match = re.search(r"<time\b[^>]*datetime=[\"'](?P<value>[^\"']+)[\"']", html_text, re.I)
    if match:
        return match.group("value")
    return clean_text(html_text)


def _json_ld_payloads(html_text: str) -> list[Any]:
    payloads = []
    for body in re.findall(
        r"<script\b(?=[^>]*type=[\"']application/ld\+json[\"'])[^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            payloads.append(json.loads(html.unescape(body.strip())))
        except json.JSONDecodeError:
            continue
    return payloads


def _find_job_postings(payload: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(payload, dict):
        item_type = payload.get("@type")
        if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
            found.append(payload)
        for value in payload.values():
            found.extend(_find_job_postings(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_find_job_postings(item))
    return found


def _identifier_value(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("value", "name", "@id"):
            if value.get(key):
                return str(value[key])
    if value:
        return str(value)
    return None


def _json_ld_location(value: object) -> str | None:
    if isinstance(value, list):
        return "; ".join(filter(None, (_json_ld_location(item) for item in value))) or None
    if not isinstance(value, dict):
        return clean_text(value)
    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        return ", ".join(str(part) for part in parts if part)
    return clean_text(value.get("name"))


def _organization_name(value: object) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("name"))
    return clean_text(value)


def _field_after(tokens: list[str], labels: tuple[str, ...]) -> str | None:
    label_set = {label.casefold() for label in labels}
    all_labels = label_set | {
        "closing date",
        "deadline",
        "deadline for application",
        "application deadline",
        "location",
        "duty station",
        "job location",
        "grade",
        "post level",
        "contract type",
        "contract",
        "type",
        "posted date",
        "date posted",
        "publication date",
        "posted on",
    }
    for index, token in enumerate(tokens):
        text = _clean(token)
        lowered = text.rstrip(":").casefold()
        for label in label_set:
            prefix = f"{label}:"
            if lowered == label:
                for next_token in tokens[index + 1 : index + 8]:
                    candidate = _clean(next_token)
                    if candidate and candidate.rstrip(":").casefold() not in all_labels:
                        return candidate
            if text.casefold().startswith(prefix):
                value = text[len(prefix) :].strip()
                if value:
                    return value
    return None


def _deadline_from_text(value: object | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for pattern in (
        r"(?:Application\s+Deadline|Application\s+deadline|Deadline|Closing\s+Date)\s*:?\s*(?P<value>\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"(?:Application\s+Deadline|Application\s+deadline|Deadline|Closing\s+Date)\s*:?\s*(?P<value>\d{1,2}/\d{1,2}/\d{4})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("value")
    return None


def _title_from_html(html_text: str) -> str | None:
    for pattern in (
        r"<h1\b[^>]*>(?P<title>.*?)</h1>",
        r"<title\b[^>]*>(?P<title>.*?)</title>",
    ):
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group("title"))
    return None


def _strip_site_suffix(value: object | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return re.split(r"\s+[|-]\s+", text, maxsplit=1)[0].strip()


def _mainish_html(html_text: str) -> str:
    detail_patterns = (
        r"<div\b(?=[^>]*class=[\"'][^\"']*\bjob_description\b)[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*id=[\"']description_box[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*id=[\"']job_details_content[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
    )
    for pattern in detail_patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group("body")
    for tag in ("main", "article"):
        match = re.search(
            rf"<{tag}\b[^>]*>(?P<body>.*?)</{tag}>",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group("body")
    return html_text


def _external_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    for key in ("job", "job_id", "id", "vacancy", "vacancy_code"):
        if query.get(key):
            return query[key]
    match = re.search(r"(?:_|/)(?P<id>\d{3,})(?:\.[a-z]+)?$", parts.path)
    if match:
        return match.group("id")
    path = parts.path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def _is_generic_link_text(value: str) -> bool:
    return value.casefold() in {
        "apply",
        "apply now",
        "apply for job",
        "apply for vacancy",
        "back",
        "careers",
        "employment",
        "home",
        "job openings",
        "jobs",
        "read more",
        "view all",
        "view job",
        "vacancies",
    }


def _dedupe(jobs: list[JobRecord]) -> list[JobRecord]:
    deduped = []
    seen = set()
    for job in jobs:
        key = job.identity_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict[str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._capture_title = False
        self._title_parts: list[str] = []
        self._ignored_tag: str | None = None

    @classmethod
    def parse(cls, html_text: str) -> "_TokenParser":
        parser = cls()
        parser.feed(html_text)
        parser.close()
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style"}:
            self._ignored_tag = tag
            return
        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key.lower()] = content
        elif tag == "title":
            self._capture_title = True
            self._title_parts = []
        elif tag == "a" and attrs_dict.get("href") and self._anchor_href is None:
            self._anchor_href = attrs_dict["href"]
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_tag == tag:
            self._ignored_tag = None
            return
        if tag == "title" and self._capture_title:
            self.title = _clean(" ".join(self._title_parts))
            self._capture_title = False
        if tag == "a" and self._anchor_href is not None:
            text = _clean(" ".join(self._anchor_text))
            if text:
                item = {"type": "a", "text": text, "href": self._anchor_href}
                self.tokens.append(item)
                self.anchors.append(item)
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_tag:
            return
        text = _clean(data)
        if not text:
            return
        if self._capture_title:
            self._title_parts.append(text)
        if self._anchor_href is not None:
            self._anchor_text.append(text)
        else:
            self.tokens.append({"type": "text", "text": text, "href": ""})
