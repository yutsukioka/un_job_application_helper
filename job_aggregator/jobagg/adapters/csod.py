"""Cornerstone / CSOD adapter."""

from __future__ import annotations

import copy
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
_CSOD_JOB_SEARCH_PATH = "rec-job-search/external/jobs"


@register_adapter
class CSODAdapter(JobAdapter):
    family = "csod"

    def fetch_jobs(self) -> list[JobRecord]:
        configured_api_url = self.source.extra.get("api_url")
        method = str(self.source.extra.get("method") or "POST").upper()
        if method == "GET":
            if not configured_api_url:
                raise ValueError(f"{self.source.id} requires extra.api_url for CSOD GET")
            return self.parse_jobs(self.fetch_json(str(configured_api_url)))

        csod_context = self._discover_context_if_configured()
        candidate_urls = self._candidate_api_urls(
            str(configured_api_url) if configured_api_url else None,
            csod_context,
        )
        if not candidate_urls:
            raise ValueError(f"{self.source.id} requires extra.api_url or CSOD context discovery")

        errors = []
        for api_url in candidate_urls:
            try:
                return self._fetch_posted_jobs(api_url, csod_context=csod_context)
            except Exception as exc:
                errors.append(f"{api_url}: {exc}")
                if not (csod_context and _looks_like_auth_error(exc)):
                    continue
                try:
                    refreshed_context = self._discover_context(required=True)
                    return self._fetch_posted_jobs(api_url, csod_context=refreshed_context)
                except Exception as refreshed_exc:
                    errors.append(f"{api_url} after token refresh: {refreshed_exc}")

        joined = " | ".join(errors[-4:])
        raise RuntimeError(f"All CSOD endpoint attempts failed for {self.source.id}: {joined}")

    def _fetch_posted_jobs(
        self,
        api_url: str,
        *,
        csod_context: dict[str, Any] | None = None,
    ) -> list[JobRecord]:
        page_size = _as_int(self.source.extra.get("page_size"), default=25)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        base_payload = copy.deepcopy(self.source.extra.get("search_payload") or {})
        base_payload.setdefault("pageNumber", 1)
        base_payload.setdefault("pageSize", page_size)
        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total_count: int | None = None

        for page_number in range(1, max_pages + 1):
            payload = copy.deepcopy(base_payload)
            payload["pageNumber"] = page_number
            payload["pageSize"] = page_size
            response_payload = self.post_json(api_url, payload, headers=self._headers(csod_context))
            page_jobs = self.parse_jobs(response_payload)
            if not page_jobs:
                break
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
            total_count = self._total_count(response_payload) or total_count
            if total_count is not None and page_number * page_size >= total_count:
                break
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = self._rows(payload)
        jobs = []
        for item in rows:
            external_id = self._external_id(item)
            apply_url = (
                item.get("companyApplyUrl")
                or item.get("url")
                or item.get("applyUrl")
                or self._apply_url_from_template(external_id)
                or str(external_id)
            )
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("title") or item.get("displayJobTitle") or item.get("displayTitle"),
                    external_id=external_id,
                    location=item.get("location") or _location_text(item),
                    department=item.get("department") or item.get("ouName"),
                    employment_type=item.get("employmentType"),
                    posted_at=item.get("postedDate") or item.get("openDate") or item.get("postingEffectiveDate"),
                    closes_at=item.get("closeDate") or item.get("postingExpirationDate"),
                    apply_url=str(apply_url),
                    description=item.get("description")
                    or item.get("externalDescription")
                    or item.get("jobDescription"),
                    raw=item,
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = self._external_id(item)
        if external_id is None:
            return None
        detail_url = self._detail_api_url(external_id)
        if detail_url is None:
            return None
        return next(iter(self.parse_jobs(self.fetch_json(detail_url))), None)

    def _rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("requisitions", "jobs", "items"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return [item for item in rows if isinstance(item, dict)]
            if data.get("displayTitle") or data.get("displayJobTitle"):
                return [data]
        for key in ("jobs", "requisitions", "items", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
        return []

    def _total_count(self, payload: Any) -> int | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        value = data.get("totalCount") if isinstance(data, dict) else payload.get("totalCount")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _detail_api_url(self, external_id: str) -> str | None:
        template = self.source.extra.get("detail_api_url_template")
        if not template:
            return None
        return str(template).format(job_id=external_id)

    def _apply_url_from_template(self, external_id: str | None) -> str | None:
        if external_id is None:
            return None
        template = self.source.extra.get("apply_url_template") or self.source.extra.get(
            "detail_url_template"
        )
        if not template:
            return None
        return str(template).format(job_id=external_id)

    def _external_id(self, item: dict[str, Any]) -> str | None:
        for key in ("requisitionId", "id", "jobId"):
            if item.get(key) is not None:
                return str(item[key])
        ref = item.get("ref")
        if ref:
            match = re.search(r"\d+", str(ref))
            return match.group(0) if match else str(ref)
        apply_url = item.get("companyApplyUrl")
        if apply_url:
            match = re.search(r"/requisition/(?P<job_id>\d+)", str(apply_url))
            if match:
                return match.group("job_id")
        return None

    def _discover_context_if_configured(self) -> dict[str, Any] | None:
        required = _as_bool(self.source.extra.get("requires_bearer_token"), default=False)
        enabled = required or _as_bool(self.source.extra.get("discover_context"), default=False)
        enabled = enabled or bool(self.source.extra.get("context_url"))
        if not enabled:
            return None
        return self._discover_context(required=required)

    def _discover_context(self, *, required: bool) -> dict[str, Any] | None:
        context_url = self._context_url()
        self.ensure_allowed(context_url)
        html = self.context.http.get(context_url, headers=self._context_page_headers()).text
        unescaped_html = html_lib.unescape(html)

        raw_context: dict[str, Any] = {}
        obj = _extract_balanced_json_object(unescaped_html, "csod.context")
        if obj:
            try:
                parsed = json.loads(obj)
                if isinstance(parsed, dict):
                    raw_context = parsed
            except json.JSONDecodeError:
                raw_context = {}

        token = _first_string(raw_context, ("token", "accessToken", "anonymousToken"))
        if not token:
            token = _regex_first(
                unescaped_html,
                (
                    r'"token"\s*:\s*"([^"]+)"',
                    r"'token'\s*:\s*'([^']+)'",
                    r"csod\.context\.token\s*=\s*['\"]([^'\"]+)['\"]",
                ),
            )
        cloud_endpoint = _cloud_endpoint(raw_context)
        if not cloud_endpoint:
            cloud_endpoint = _regex_first(
                unescaped_html,
                (
                    r'"cloud"\s*:\s*"([^"]+)"',
                    r"'cloud'\s*:\s*'([^']+)'",
                    r"csod\.context\.endpoints\.cloud\s*=\s*['\"]([^'\"]+)['\"]",
                ),
            )

        if not token:
            if required:
                raise RuntimeError(
                    "Could not find CSOD anonymous token in the career page. "
                    "Capture the listing XHR in DevTools or use Playwright to inspect "
                    "window.csod.context."
                )
            return None

        return {
            "token": token,
            "cloud_endpoint": cloud_endpoint.rstrip("/") if cloud_endpoint else None,
            "raw_context": raw_context,
        }

    def _candidate_api_urls(
        self,
        configured_api_url: str | None,
        csod_context: dict[str, Any] | None,
    ) -> list[str]:
        urls: list[str] = []
        cloud_endpoint = csod_context.get("cloud_endpoint") if csod_context else None
        if cloud_endpoint:
            urls.append(urljoin(f"{cloud_endpoint.rstrip('/')}/", _CSOD_JOB_SEARCH_PATH))
        if configured_api_url:
            urls.append(configured_api_url)
        legacy_api_url = self.source.extra.get("legacy_api_url")
        if legacy_api_url:
            urls.append(str(legacy_api_url))
        elif csod_context or _as_bool(self.source.extra.get("requires_bearer_token"), default=False):
            origin = _origin(self._context_url())
            if origin:
                urls.append(f"{origin}/services/x/career-site/v1/search")
        return _dedupe(urls)

    def _context_url(self) -> str:
        return str(
            self.source.extra.get("context_url")
            or self.source.extra.get("career_url")
            or self.source.base_url
        )

    def _culture_name(self) -> str:
        payload = self.source.extra.get("search_payload")
        if isinstance(payload, dict) and payload.get("cultureName"):
            return str(payload["cultureName"])
        return str(self.source.extra.get("culture_name") or "en-US")

    def _context_page_headers(self) -> dict[str, str]:
        return {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def _headers(self, csod_context: dict[str, Any] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json; q=1.0, text/*; q=0.8, */*; q=0.1",
            "Referer": self._context_url(),
        }
        if not csod_context:
            return headers

        headers.update(
            {
                "User-Agent": _BROWSER_USER_AGENT,
                "Authorization": f"Bearer {csod_context['token']}",
                "Origin": _origin(self._context_url()) or self.source.base_url,
                "Csod-Accept-Language": self._culture_name(),
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        return headers


def _location_text(item: dict[str, Any]) -> str | None:
    primary = item.get("primaryLocation")
    if isinstance(primary, dict):
        return primary.get("title") or ", ".join(
            str(primary[key]) for key in ("city", "state", "country") if primary.get(key)
        )
    if primary:
        return str(primary)
    locations = item.get("locations")
    if isinstance(locations, list):
        parts = []
        for location in locations:
            if isinstance(location, dict):
                text = ", ".join(
                    str(location[key]) for key in ("city", "state", "country") if location.get(key)
                )
                if text:
                    parts.append(text)
            elif location:
                parts.append(str(location))
        return "; ".join(parts) or None
    return None


def _extract_balanced_json_object(text: str, marker: str) -> str | None:
    """Extract a JSON object appearing after marker, preserving nested braces."""

    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("{", idx)
    if start == -1:
        return None

    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in {"'", '"'}:
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item:
                return item
        for item in value.values():
            found = _first_string(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_string(item, keys)
            if found:
                return found
    return None


def _cloud_endpoint(context: dict[str, Any]) -> str | None:
    endpoints = context.get("endpoints")
    if isinstance(endpoints, dict):
        for key in ("cloud", "cloudEndpoint"):
            value = endpoints.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
    value = _first_string(context, ("cloudEndpoint", "cloud"))
    if value and value.startswith("http"):
        return value
    return None


def _regex_first(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _origin(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _dedupe(urls: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def _looks_like_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http 401" in text or "unauthorized" in text or "authorization" in text
