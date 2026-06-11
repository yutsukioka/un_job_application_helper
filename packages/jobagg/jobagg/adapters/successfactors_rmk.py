"""SAP SuccessFactors / RMK-style career site adapter."""

from __future__ import annotations

import copy
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html as _clean_html

_JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_JOB_LINK_RE = re.compile(
    r'<a(?=[^>]*class="[^"]*\bjobTitle-link\b[^"]*")'
    r'(?=[^>]*href="(?P<href>[^"]+)")[^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_LEGACY_JOB_LINK_RE = re.compile(
    r"<a\b(?=[^>]*href=[\"'](?P<href>[^\"']*(?:career_job_req_id|jobReqId|jobreqid|jobId|job_id)[^\"']*)[\"'])[^>]*>"
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_ROW_RE = re.compile(
    r'<tr[^>]+class="[^"]*\bdata-row\b[^"]*"[^>]*>(?P<html>.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
_TILE_RE = re.compile(
    r'<li[^>]+class="[^"]*\bjob-tile\b[^"]*"[^>]*>(?P<html>.*?)(?=<li[^>]+class="[^"]*\bjob-tile\b|</ul>)',
    re.IGNORECASE | re.DOTALL,
)
_DESCRIPTION_START_RE = re.compile(
    r"<(?P<tag>span|div|section)\b"
    r"(?=[^>]*(?:itemprop=[\"']description[\"']|class=[\"'][^\"']*\bjobdescription\b[^\"']*[\"']))"
    r"[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_START_RE = re.compile(
    r"<(?P<tag>div|section)\b(?=[^>]*class=[\"'][^\"']*\bjobDisplay\b[^\"']*[\"'])[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_AIIB_CURRENT_JOB_RE = re.compile(
    r"jobs\[(?P<index>\d+)\]\[\"(?P<key>[^\"]+)\"\]\s*=\s*\"(?P<value>(?:\\.|[^\"\\])*)\";",
    re.IGNORECASE,
)
_AIIB_FONT_COPY_RE = re.compile(
    r"<div\b(?=[^>]*class=[\"'][^\"']*\bfont-copy-18-black\b[^\"']*[\"'])[^>]*>(?P<body>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)


@register_adapter
class SuccessFactorsRMKAdapter(JobAdapter):
    family = "successfactors_rmk"

    def fetch_jobs(self) -> list[JobRecord]:
        rss_url = self.source.extra.get("rss_url")
        if rss_url:
            return self.parse_jobs_from_rss(self.fetch_text(str(rss_url)))
        api_url = self.source.extra.get("api_url")
        if api_url:
            return self._fetch_api_jobs(str(api_url))
        url = str(self.source.extra.get("search_url") or self._default_search_url())
        return self._fetch_html_jobs(url)

    def _fetch_api_jobs(self, api_url: str) -> list[JobRecord]:
        page_size = _as_int(self.source.extra.get("page_size"), default=10)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        base_payload = copy.deepcopy(self.source.extra.get("search_payload") or {})
        base_payload.setdefault("pageNumber", 0)
        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total_jobs: int | None = None
        for page_number in range(max_pages):
            payload = copy.deepcopy(base_payload)
            payload["pageNumber"] = page_number
            response_payload = self.post_json(api_url, payload, headers=self._headers())
            page_jobs = self.parse_jobs_from_api(response_payload)
            if not page_jobs:
                break
            new_in_page = 0
            for job in page_jobs:
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
                new_in_page += 1
            if new_in_page == 0:
                # All rows on this page were duplicates of jobs we already
                # collected; stop to avoid an infinite loop on a vendor
                # paging bug or shrinking result set.
                break
            total_jobs = _as_int(response_payload.get("totalJobs"), default=total_jobs or 0) if isinstance(response_payload, dict) else total_jobs
            if total_jobs is not None and (page_number + 1) * page_size >= total_jobs:
                break
        return jobs

    def _fetch_html_jobs(self, url: str) -> list[JobRecord]:
        max_pages = _as_int(self.source.extra.get("max_pages"), default=5)
        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        seen_urls: set[str] = set()
        current_url: str | None = url
        for _ in range(max_pages):
            if not current_url or current_url in seen_urls:
                break
            seen_urls.add(current_url)
            html_text = self.fetch_text(current_url)
            page_jobs = self.parse_jobs_from_html(html_text)
            if not page_jobs:
                break
            for job in page_jobs:
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
            current_url = _next_page_url(html_text, current_url)
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        if item.get("parser") == "aiib_current_jobs_js":
            return self._fetch_aiib_detail_for_listing_item(item)
        detail_url = _raw_detail_url(item)
        if not detail_url:
            external_id = item.get("id") or item.get("jobReqId") or item.get("jobreqid")
            url_title = item.get("urlTitle") or item.get("unifiedUrlTitle")
            detail_url = self._detail_url(external_id, url_title)
        if not detail_url:
            return None
        detail_url = str(detail_url)
        self.ensure_allowed(detail_url)
        html_text = self.fetch_text(detail_url)
        for job in self.parse_jobs_from_html(html_text):
            if job.description and _is_meaningful_description(job.description):
                return job
        description = _detail_description(html_text)
        if not description:
            return None
        return build_job(
            self.source,
            title=_detail_title(html_text) or _raw_title(item),
            external_id=_job_id_from_url(detail_url),
            location=_detail_location(html_text) or _raw_location(item),
            department=_raw_department(item),
            employment_type=_raw_employment_type(item),
            posted_at=_raw_posted_at(item) or _detail_posted_at(html_text),
            closes_at=_raw_closes_at(item) or _detail_closes_at(html_text) or _application_deadline(html_text),
            apply_url=detail_url,
            source_url=detail_url,
            description=description,
            raw={**item, "detail_url": detail_url, "parser": "successfactors_detail"},
        )

    def _fetch_aiib_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("detail_url") or item.get("source_url") or item.get("apply_url")
        if not detail_url:
            return None
        detail_url = str(detail_url)
        self.ensure_allowed(detail_url)
        html_text = self.fetch_text(detail_url)
        description = _aiib_detail_description(html_text)
        if not description:
            return None
        fields = _aiib_detail_fields(html_text)
        apply_url = _aiib_apply_url(html_text) or item.get("apply_url") or detail_url
        successfactors_id = _job_id_from_url(str(apply_url)) if apply_url else None
        return build_job(
            self.source,
            title=_raw_title(item) or _detail_title(html_text) or fields.get("Position"),
            external_id=item.get("external_id") or item.get("number") or _job_id_from_url(detail_url),
            location=fields.get("Location") or _raw_location(item),
            department=fields.get("Department/Division") or _raw_department(item),
            employment_type=fields.get("Job Type **") or fields.get("Job Type") or _raw_employment_type(item),
            posted_at=fields.get("Posting Date") or _raw_posted_at(item),
            closes_at=fields.get("Closing Date *") or fields.get("Closing Date") or _raw_closes_at(item),
            apply_url=str(apply_url),
            source_url=detail_url,
            description=description,
            raw={
                **item,
                "detail_url": detail_url,
                "successfactors_job_id": successfactors_id,
                "detail_fields": fields,
                "parser": "aiib_official_detail",
            },
        )

    def parse_jobs_from_api(self, payload: Any) -> list[JobRecord]:
        rows = payload.get("jobSearchResult", []) if isinstance(payload, dict) else payload
        jobs: list[JobRecord] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            item = row.get("response") if isinstance(row.get("response"), dict) else row
            external_id = item.get("id")
            url_title = item.get("urlTitle") or item.get("unifiedUrlTitle")
            detail_url = self._detail_url(external_id, url_title)
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("unifiedStandardTitle") or item.get("title"),
                    external_id=external_id,
                    location=_first_text(item.get("jobLocationShort"))
                    or _first_text(item.get("mfield1")),
                    department=_first_text(item.get("legalEntity_obj")) or _first_text(item.get("filter6")),
                    employment_type=item.get("jobGrade") or _first_text(item.get("filter3")),
                    posted_at=item.get("unifiedStandardStart") or item.get("cus_postingdate"),
                    closes_at=item.get("unifiedStandardEnd") or item.get("cus_enddate"),
                    apply_url=detail_url or str(external_id),
                    source_url=detail_url,
                    raw={**item, "detail_url": detail_url},
                )
            )
        return jobs

    def parse_jobs_from_rss(self, rss_text: str) -> list[JobRecord]:
        root = ET.fromstring(rss_text)
        jobs: list[JobRecord] = []
        empty_placeholders = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title")
            link = item.findtext("link") or item.findtext("guid")
            description = item.findtext("description")
            if not title or not link:
                continue
            if _is_empty_rss_item(title, description, link):
                empty_placeholders.append(
                    {
                        "title": title,
                        "link": link,
                        "description": description,
                    }
                )
                continue
            jobs.append(
                build_job(
                    self.source,
                    title=_title_without_location(title),
                    external_id=_job_id_from_url(link),
                    location=_location_from_title(title),
                    department=_labeled_value(description, "Department"),
                    employment_type=_labeled_value(description, "Grade"),
                    posted_at=_labeled_value(description, "Publication date") or item.findtext("pubDate"),
                    closes_at=_application_deadline(description),
                    apply_url=link,
                    source_url=link,
                    description=description,
                    raw={
                        "title": title,
                        "link": link,
                        "description": description,
                        "pubDate": item.findtext("pubDate"),
                    },
                )
            )
        if not jobs and empty_placeholders:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_text_empty"
            self.run_diagnostics.zero_fetched_evidence = {
                "matched_text": empty_placeholders[0]["title"],
                "items": empty_placeholders[:3],
            }
            self.run_diagnostics.pagination_complete = True
        return jobs

    def parse_jobs_from_html(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        jobs.extend(self._parse_legacy_links(html_text))
        jobs.extend(self._parse_table_rows(html_text))
        jobs.extend(self._parse_tiles(html_text))
        for match in _JSON_LD_RE.finditer(html_text):
            try:
                payload = json.loads(match.group("body"))
            except json.JSONDecodeError:
                continue
            rows = payload if isinstance(payload, list) else [payload]
            for item in rows:
                if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                    continue
                identifier = _identifier_value(item.get("identifier"))
                apply_url = item.get("url") or identifier
                jobs.append(
                    build_job(
                        self.source,
                        title=item.get("title"),
                        external_id=identifier,
                        location=_location_text(item.get("jobLocation")),
                        employment_type=item.get("employmentType"),
                        posted_at=item.get("datePosted"),
                        closes_at=item.get("validThrough"),
                        apply_url=str(apply_url),
                        description=item.get("description"),
                        raw=item,
                    )
                )
        deduped: list[JobRecord] = []
        seen: set[str] = set()
        for job in jobs:
            key = job.identity_key()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(job)
        return deduped

    def _parse_legacy_links(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for match in _LEGACY_JOB_LINK_RE.finditer(html_text):
            href = html.unescape(match.group("href"))
            detail_url = urljoin(self.source.base_url, href)
            title = _clean_html(match.group("title"))
            if not title or title.casefold() in {"view", "apply", "job details"}:
                continue
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=_job_id_from_url(detail_url),
                    apply_url=detail_url,
                    source_url=detail_url,
                    raw={
                        "listing_html": match.group(0),
                        "detail_url": detail_url,
                        "title": title,
                        "parser": "successfactors_legacy",
                    },
                )
            )
        return jobs

    def _parse_table_rows(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for match in _TABLE_ROW_RE.finditer(html_text):
            item_html = match.group("html")
            link = _JOB_LINK_RE.search(item_html)
            if not link:
                continue
            href = html.unescape(link.group("href"))
            detail_url = urljoin(self.source.base_url, href)
            jobs.append(
                build_job(
                    self.source,
                    title=_clean_html(link.group("title")),
                    external_id=_job_id_from_url(detail_url),
                    location=_extract_class_text(item_html, "jobLocation"),
                    department=_extract_class_text(item_html, "jobDepartment"),
                    employment_type=_extract_class_text(item_html, "jobFacility"),
                    posted_at=_extract_class_text(item_html, "jobDate"),
                    closes_at=_extract_class_text(item_html, "jobShifttype"),
                    apply_url=detail_url,
                    source_url=detail_url,
                    raw={
                        "listing_html": item_html,
                        "detail_url": detail_url,
                        "title": _clean_html(link.group("title")),
                    },
                )
            )
        return jobs

    def _parse_tiles(self, html_text: str) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for match in _TILE_RE.finditer(html_text):
            item_html = match.group("html")
            link = _JOB_LINK_RE.search(item_html)
            if not link:
                continue
            href = html.unescape(link.group("href"))
            detail_url = urljoin(self.source.base_url, href)
            location = _extract_section_value(item_html, "customfield4") or _extract_section_value(
                item_html, "location"
            )
            country = _extract_section_value(item_html, "dept")
            if country and country not in (location or ""):
                location = ", ".join(filter(None, [location, country]))
            jobs.append(
                build_job(
                    self.source,
                    title=_clean_html(link.group("title")),
                    external_id=_job_id_from_url(detail_url),
                    location=location,
                    department=_extract_section_value(item_html, "facility"),
                    employment_type=_extract_section_value(item_html, "shifttype"),
                    closes_at=_extract_section_value(item_html, "date")
                    or _extract_section_value(item_html, "postingdate"),
                    apply_url=detail_url,
                    source_url=detail_url,
                    raw={
                        "listing_html": item_html,
                        "detail_url": detail_url,
                        "title": _clean_html(link.group("title")),
                    },
                )
            )
        return jobs

    def _default_search_url(self) -> str:
        path = str(self.source.extra.get("careers_path") or "search/?q=")
        return f"{self.source.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _detail_url(self, external_id: Any, url_title: Any) -> str | None:
        if external_id is None:
            return None
        template = self.source.extra.get("detail_url_template")
        if template:
            return str(template).format(job_id=external_id, url_title=url_title or external_id)
        if url_title:
            return urljoin(self.source.base_url, f"/job/{url_title}/{external_id}/")
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Referer": str(self.source.extra.get("search_url") or self.source.base_url),
        }


def _identifier_value(value: object) -> str | None:
    if isinstance(value, dict):
        identifier = value.get("value") or value.get("name")
        return str(identifier) if identifier else None
    if isinstance(value, str):
        return value
    return None


def _location_text(value: object) -> str | None:
    if isinstance(value, dict):
        address = value.get("address")
        if isinstance(address, dict):
            return ", ".join(str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
    if isinstance(value, list):
        return "; ".join(filter(None, (_location_text(item) for item in value)))
    return None


def _raw_detail_url(item: dict[str, Any]) -> str | None:
    for key in ("detail_url", "href", "link", "url", "apply_url", "source_url"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _is_empty_rss_item(title: str, description: str | None, link: str) -> bool:
    text = f"{title} {description or ''} {link}".casefold()
    return (
        "no jobs currently available" in text
        or "no jobs available" in text
        or "check out our other opportunities" in text
    )


def _raw_title(item: dict[str, Any]) -> str | None:
    for key in ("title", "unifiedStandardTitle", "jobTitle", "externalJobTitle"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _raw_location(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("jobLocationShort")) or _first_text(item.get("mfield1"))


def _raw_department(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("legalEntity_obj")) or _first_text(item.get("filter6"))


def _raw_employment_type(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("jobGrade")) or _first_text(item.get("filter3"))


def _raw_posted_at(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("unifiedStandardStart")) or _first_text(item.get("cus_postingdate"))


def _raw_closes_at(item: dict[str, Any]) -> str | None:
    return _first_text(item.get("unifiedStandardEnd")) or _first_text(item.get("cus_enddate"))


def _detail_title(html_text: str) -> str | None:
    title = _first_html_match(
        html_text,
        (
            r"<meta\b(?=[^>]*property=[\"']og:title[\"'])(?=[^>]*content=[\"'](?P<value>[^\"']+)[\"'])",
            r"<h1\b[^>]*>(?P<value>.*?)</h1>",
            r"<title\b[^>]*>(?P<value>.*?)</title>",
        ),
    )
    if not title:
        return None
    return re.split(r"\s+[|-]\s+", title, maxsplit=1)[0].strip()


def _detail_location(html_text: str) -> str | None:
    return _first_html_match(
        html_text,
        (
            r"<meta\b(?=[^>]*itemprop=[\"']streetAddress[\"'])(?=[^>]*content=[\"'](?P<value>[^\"']+)[\"'])",
            r"<span\b(?=[^>]*class=[\"'][^\"']*\bjobLocation\b)[^>]*>(?P<value>.*?)</span>",
            r"<div\b(?=[^>]*class=[\"'][^\"']*\blocation\b)[^>]*>(?P<value>.*?)</div>",
        ),
    )


def _detail_posted_at(html_text: str) -> str | None:
    return _sf_meta_datetime(_itemprop_meta_content(html_text, "datePosted"))


def _detail_closes_at(html_text: str) -> str | None:
    return _sf_meta_datetime(_itemprop_meta_content(html_text, "validThrough"))


def _detail_description(html_text: str) -> str | None:
    candidates = _description_candidates(html_text, _DESCRIPTION_START_RE)
    meaningful = [candidate for candidate in candidates if _is_meaningful_description(candidate)]
    if meaningful:
        return max(meaningful, key=len)

    fallback_candidates = _description_candidates(html_text, _DISPLAY_START_RE)
    meaningful = [candidate for candidate in fallback_candidates if _is_meaningful_description(candidate)]
    if meaningful:
        return max(meaningful, key=len)
    return None


def _aiib_detail_description(html_text: str) -> str | None:
    sections: list[str] = []
    for match in _AIIB_FONT_COPY_RE.finditer(html_text):
        body = match.group("body")
        cleaned = _clean_html(body)
        if not _is_meaningful_description(cleaned):
            continue
        heading = _aiib_preceding_heading(html_text[: match.start()])
        sections.append(f"{heading}\n{cleaned}" if heading else cleaned)
    if sections:
        return "\n\n".join(sections)

    fallback = _description_candidates(
        html_text,
        re.compile(
            r"<(?P<tag>div)\b(?=[^>]*class=[\"'][^\"']*\bexternalPosting\b[^\"']*[\"'])[^>]*>",
            re.IGNORECASE | re.DOTALL,
        ),
    )
    meaningful = [candidate for candidate in fallback if _is_meaningful_description(candidate)]
    if meaningful:
        return max(meaningful, key=len)
    return None


def _aiib_preceding_heading(prefix: str) -> str | None:
    match = re.search(
        r"<h2\b(?=[^>]*class=[\"'][^\"']*\bsubheadline\b[^\"']*[\"'])[^>]*>(?P<value>.*?)</h2>\s*$",
        prefix[-500:],
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _clean_html(match.group("value")) if match else None


def _aiib_detail_fields(html_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pattern = re.compile(
        r"<div\b(?=[^>]*class=[\"'][^\"']*\bitem\b[^\"']*[\"'])[^>]*>\s*"
        r"<div\b(?=[^>]*class=[\"'][^\"']*\bcol-title\b[^\"']*[\"'])[^>]*>(?P<label>.*?)</div>\s*"
        r"<div\b(?=[^>]*class=[\"'][^\"']*\bcol-con\b[^\"']*[\"'])[^>]*>(?P<value>.*?)</div>\s*"
        r"</div>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        label = _clean_html(match.group("label"))
        value = _clean_html(match.group("value"))
        if label and value:
            fields[label] = value
    return fields


def _aiib_apply_url(html_text: str) -> str | None:
    match = re.search(
        r"<a\b(?=[^>]*href=[\"'](?P<href>[^\"']*career5\.successfactors\.eu[^\"']*(?:jobreqcareer|career_job_req_id|jobId)[^\"']*)[\"'])[^>]*>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return html.unescape(match.group("href")) if match else None


def _strip_js_block_comments(value: str) -> str:
    return re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)


def _decode_js_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return html.unescape(value.replace("\\/", "/").replace('\\"', '"'))


def _description_candidates(html_text: str, pattern: re.Pattern[str]) -> list[str]:
    candidates = []
    for match in pattern.finditer(html_text):
        body = _balanced_element_inner_html(html_text, match)
        cleaned = _clean_html(body)
        if cleaned:
            candidates.append(cleaned)
    return candidates


def _balanced_element_inner_html(html_text: str, match: re.Match[str]) -> str:
    tag = match.group("tag").lower()
    inner_start = match.end()
    tag_re = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    depth = 1
    for tag_match in tag_re.finditer(html_text, inner_start):
        token = tag_match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return html_text[inner_start : tag_match.start()]
        elif not token.endswith("/>"):
            depth += 1
    return html_text[inner_start:]


def _is_meaningful_description(value: str | None) -> bool:
    text = (value or "").strip()
    if len(text) >= 120:
        return True
    lowered = text.casefold()
    if lowered.startswith(("grade:", "requisition id", "vacancy notice")):
        return False
    return len(text.split()) >= 18


def _itemprop_meta_content(html_text: str, itemprop: str) -> str | None:
    pattern = (
        rf"<meta\b(?=[^>]*itemprop=[\"']{re.escape(itemprop)}[\"'])"
        r"(?=[^>]*content=[\"'](?P<value>[^\"']+)[\"'])"
    )
    match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group("value")).strip() if match else None


def _sf_meta_datetime(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%a %b %d %H:%M:%S UTC %Y", "%a %b %d %H:%M:%S GMT %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return text


def _first_html_match(
    html_text: str,
    patterns: tuple[str, ...],
    *,
    raw: bool = False,
) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = match.group("value")
        if raw:
            return value
        cleaned = _clean_html(value)
        if cleaned:
            return cleaned
    return None


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        return "; ".join(str(item).strip() for item in value if str(item).strip()) or None
    if value is None:
        return None
    return str(value)


def _extract_class_text(html_text: str, class_name: str) -> str | None:
    pattern = re.compile(
        rf'<span[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(?P<value>.*?)</span>',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    return _clean_html(match.group("value")) if match else None


def _extract_section_value(html_text: str, class_name: str) -> str | None:
    pattern = re.compile(
        rf'<div[^>]+class="[^"]*\bsection-field\b[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>'
        r"(?P<body>.*?)</div>\s*</div>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    if not match:
        return None
    body = match.group("body")
    value = re.search(r'<div[^>]+id="[^"]+-value"[^>]*>(?P<value>.*?)</div>', body, re.I | re.S)
    return _clean_html(value.group("value") if value else body)


def _job_id_from_url(url: str) -> str | None:
    query = dict(parse_qsl(urlsplit(url).query))
    for key in ("career_job_req_id", "jobReqId", "jobreqid", "jobId", "job_id", "job"):
        if query.get(key):
            return query[key]
    parts = [part for part in urlsplit(url).path.rstrip("/").split("/") if part]
    if not parts:
        return None
    for part in reversed(parts):
        match = re.search(r"(?P<job_id>\d{4,})(?:-[a-z]{2}_[A-Z]{2})?$", part)
        if match:
            return match.group("job_id")
    return parts[-1]


def _xml_job_elements(root: ET.Element) -> list[ET.Element]:
    candidates = []
    for element in root.iter():
        tag = _xml_key(element.tag)
        fields = _xml_flatten(element)
        if tag in {"job", "jobposting", "jobrequisition"}:
            candidates.append(element)
            continue
        if _first_field(fields, ("title", "jobtitle", "externaljobtitle")) and _first_field(
            fields,
            ("jobreqid", "jobid", "id", "career_job_req_id"),
        ):
            candidates.append(element)
    deduped: list[ET.Element] = []
    seen: set[int] = set()
    for element in candidates:
        element_id = id(element)
        if element_id in seen:
            continue
        seen.add(element_id)
        deduped.append(element)
    return deduped


def _xml_flatten(element: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in element.attrib.items():
        text = str(value).strip()
        if text:
            fields.setdefault(_xml_key(key), text)
    for child in element.iter():
        if child is element:
            continue
        key = _xml_key(child.tag)
        text = "".join(child.itertext()).strip()
        if key and text:
            fields.setdefault(key, _clean_html(text) or text)
    return fields


def _xml_total_count(root: ET.Element) -> int | None:
    for key, value in _xml_flatten(root).items():
        if key in {"total", "totaljobs", "totaljobcount", "count", "postingcount", "resultcount"}:
            try:
                return int(value)
            except ValueError:
                return None
    for key, value in root.attrib.items():
        if _xml_key(key) in {"total", "totaljobs", "totaljobcount", "count", "postingcount", "resultcount"}:
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _xml_zero_evidence(root: ET.Element) -> dict[str, Any]:
    total = _xml_total_count(root)
    if total == 0:
        return {"total_reported_by_source": 0}
    if _xml_key(root.tag) in {"joblisting", "job-listing"} and not _xml_job_elements(root):
        return {"root": _xml_key(root.tag), "job_elements": 0}
    return {}


def _first_field(fields: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(_xml_key(key))
        if value not in (None, ""):
            return value
    return None


def _xml_key(value: object) -> str:
    text = str(value)
    if "}" in text:
        text = text.rsplit("}", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


@register_adapter
class SuccessFactorsLegacyAdapter(SuccessFactorsRMKAdapter):
    family = "successfactors_legacy"

    def fetch_jobs(self) -> list[JobRecord]:
        if self.source.extra.get("listing_feed_type") == "aiib_current_jobs_js":
            return self._fetch_aiib_current_jobs()
        if self.source.extra.get("primary_fetch_method") == "successfactors_xml":
            return self._fetch_xml_feed_jobs()
        return super().fetch_jobs()

    def _fetch_aiib_current_jobs(self) -> list[JobRecord]:
        self.run_diagnostics.fetch_method = "aiib_current_jobs_js"
        self.run_diagnostics.endpoint_family = "aiib_official_static"
        self.run_diagnostics.scope_validation_status = "passed"
        feed_url = str(self.source.extra.get("current_jobs_url") or "")
        if not feed_url:
            official_listing_url = str(self.source.extra.get("official_listing_url") or "")
            feed_url = urljoin(official_listing_url, ".content/index/current-jobs.js")
        if not feed_url:
            raise RuntimeError(f"{self.source.id}: AIIB current jobs feed URL is not configured")
        jobs = self.parse_aiib_current_jobs_js(self.fetch_text(feed_url), feed_url)
        self.run_diagnostics.total_reported_by_source = len(jobs)
        self.run_diagnostics.pages_fetched = 1
        self.run_diagnostics.pagination_complete = True
        if jobs:
            self.run_diagnostics.health_status = "ok"
        else:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_text_empty"
            self.run_diagnostics.zero_fetched_evidence = {"feed_url": feed_url, "jobs": 0}
        return jobs

    def parse_aiib_current_jobs_js(self, js_text: str, feed_url: str | None = None) -> list[JobRecord]:
        rows: dict[int, dict[str, str]] = {}
        js_text = _strip_js_block_comments(js_text)
        for match in _AIIB_CURRENT_JOB_RE.finditer(js_text):
            index = int(match.group("index"))
            rows.setdefault(index, {})[match.group("key")] = _decode_js_string(match.group("value"))
        jobs: list[JobRecord] = []
        for index in sorted(rows):
            row = rows[index]
            title = row.get("title")
            path = row.get("path")
            if not title or not path:
                continue
            detail_url = urljoin(str(self.source.extra.get("official_listing_url") or feed_url or self.source.base_url), path)
            external_id = row.get("number") or _job_id_from_url(detail_url)
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=external_id,
                    location=row.get("location"),
                    department=row.get("department"),
                    employment_type=row.get("type"),
                    posted_at=row.get("positioning-date"),
                    closes_at=row.get("closing-date"),
                    apply_url=detail_url,
                    source_url=detail_url,
                    description=row.get("description"),
                    raw={
                        **row,
                        "detail_url": detail_url,
                        "feed_url": feed_url,
                        "parser": "aiib_current_jobs_js",
                    },
                )
            )
        return jobs

    def _fetch_xml_feed_jobs(self) -> list[JobRecord]:
        self.run_diagnostics.fetch_method = "successfactors_xml"
        self.run_diagnostics.endpoint_family = "successfactors_rcm"
        self.run_diagnostics.scope_validation_status = "passed"
        feed_url = self._xml_feed_url()
        xml_text = self.fetch_text(feed_url)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            self.run_diagnostics.health_status = "issue"
            self.run_diagnostics.empty_reason = "xml_unavailable"
            raise RuntimeError(
                f"{self.source.id}: SuccessFactors XML feed unavailable or not XML"
            ) from exc

        jobs = self.parse_jobs_from_xml_feed(root)
        total_count = _xml_total_count(root)
        self.run_diagnostics.total_reported_by_source = total_count if total_count is not None else len(jobs)
        self.run_diagnostics.pages_fetched = 1
        self.run_diagnostics.pagination_complete = True
        if jobs:
            self.run_diagnostics.health_status = "ok"
            return jobs

        zero_evidence = _xml_zero_evidence(root)
        if zero_evidence:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = zero_evidence
            return []

        self.run_diagnostics.health_status = "issue"
        self.run_diagnostics.empty_reason = "unverified_zero"
        raise RuntimeError(
            f"{self.source.id}: SuccessFactors XML returned zero jobs without verified zero evidence"
        )

    def parse_jobs_from_xml_feed(self, root: ET.Element) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        seen: set[str] = set()
        for item in _xml_job_elements(root):
            fields = _xml_flatten(item)
            external_id = _first_field(
                fields,
                (
                    "jobreqid",
                    "jobreq_id",
                    "jobrequisitionid",
                    "career_job_req_id",
                    "jobid",
                    "id",
                    "reqid",
                ),
            )
            title = _first_field(fields, ("title", "jobtitle", "externaljobtitle", "job_title"))
            if not external_id or not title:
                continue
            if external_id in seen:
                continue
            seen.add(external_id)
            source_url = (
                _first_field(fields, ("url", "link", "joburl", "applyurl"))
                or self._successfactors_detail_url(external_id)
            )
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=external_id,
                    location=_first_field(fields, ("location", "joblocation", "city", "country")),
                    department=_first_field(fields, ("department", "jobdepartment", "facility")),
                    employment_type=_first_field(fields, ("grade", "jobgrade", "shifttype", "employmenttype")),
                    posted_at=_first_field(fields, ("posteddate", "postingdate", "startdate")),
                    closes_at=_first_field(fields, ("closingdate", "enddate", "applicationdeadline")),
                    apply_url=source_url,
                    source_url=source_url,
                    description=_first_field(fields, ("description", "jobdescription")),
                    raw={**fields, "parser": "successfactors_xml"},
                )
            )
        return jobs

    def _xml_feed_url(self) -> str:
        configured = self.source.extra.get("xml_feed_url")
        if configured:
            return str(configured)
        company_id = str(self.source.extra.get("company_id") or "")
        locale = str(self.source.extra.get("locale") or "en_GB")
        base_parts = urlsplit(self.source.base_url)
        base_path = base_parts.path or "/career"
        query = dict(parse_qsl(base_parts.query, keep_blank_values=True))
        query.update(
            {
                "company": company_id or query.get("company", ""),
                "career_ns": "job_listing_summary",
                "resultType": "XML",
                "rcm_site_locale": locale,
                "selected_lang": locale,
            }
        )
        return urlunsplit(
            (
                base_parts.scheme,
                base_parts.netloc,
                base_path,
                urlencode({key: value for key, value in query.items() if value != ""}),
                "",
            )
        )

    def _successfactors_detail_url(self, external_id: str) -> str:
        company_id = str(self.source.extra.get("company_id") or "")
        locale = str(self.source.extra.get("locale") or "en_GB")
        parts = urlsplit(self.source.base_url)
        query = {
            "company": company_id,
            "career_ns": "job_listing",
            "career_job_req_id": external_id,
            "rcm_site_locale": locale,
            "selected_lang": locale,
        }
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/career", urlencode(query), ""))


def _title_without_location(title: str) -> str:
    return re.sub(r"\s+\([^()]+,\s*[A-Z]{2,3}\)\s*$", "", title).strip()


def _location_from_title(title: str) -> str | None:
    match = re.search(r"\((?P<location>[^()]+,\s*[A-Z]{2,3})\)\s*$", title)
    return match.group("location") if match else None


def _labeled_value(html_text: str | None, label: str) -> str | None:
    if not html_text:
        return None
    pattern = re.compile(
        rf"{re.escape(label)}\s*:?\s*</strong>\s*(?P<value>.*?)(?:<br|</p>|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(html_text)
    return _clean_html(match.group("value")) if match else None


def _application_deadline(html_text: str | None) -> str | None:
    if not html_text:
        return None
    match = re.search(
        r"Application deadline.*?:\s*(?:<[^>]+>)*\s*(?P<value>\d{1,2}\s+\w+\s+\d{4})",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group("value") if match else None


def _next_page_url(html_text: str, current_url: str) -> str | None:
    current_start = _startrow(current_url)
    candidates: list[tuple[int, str]] = []
    for pattern in (
        r'href="(?P<href>[^"]*startrow=\d+[^"]*)"',
        r'href="(?P<href>[^"]*/go/[^"]*/\d+/\d+/?[^"]*)"',
    ):
        for match in re.finditer(pattern, html_text, re.I):
            href = html.unescape(match.group("href"))
            url = urljoin(current_url, href)
            start = _startrow(url)
            if start > current_start:
                candidates.append((start, url))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _startrow(url: str) -> int:
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    try:
        query_start = query.get("startrow")
        if query_start:
            return int(query_start)
        path_match = re.search(r"/go/[^/]+/\d+/(?P<start>\d+)/?$", urlsplit(url).path)
        if path_match:
            return int(path_match.group("start"))
        return 0
    except ValueError:
        return 0
