"""Oracle Fusion Cloud HCM Candidate Experience adapter."""

from __future__ import annotations

import re
import socket
import uuid
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job, parse_datetime
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


_EXPAND = (
    "requisitionList.workLocation,requisitionList.otherWorkLocations,"
    "requisitionList.secondaryLocations,flexFieldsFacet.values,"
    "requisitionList.requisitionFlexFields"
)
_FACETS = "LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS"
_OFFICIAL_LISTING_STATUS = "public_listing_available_oracle_apply_link_unverified"
_FRAUD_WARNING = (
    "UNDP/UNFPA do not charge application, processing, training, interview, "
    "testing, or other recruitment fees. Use official vacancy pages and beware "
    "of fraudulent vacancy announcements."
)

_SPACE_RE = re.compile(r"\s+")
_HTTP_STATUS_RE = re.compile(r"http\s+(\d{3})", re.IGNORECASE)


@register_adapter
class OracleHCMAdapter(JobAdapter):
    family = "oracle_hcm"

    def fetch_jobs(self) -> list[JobRecord]:
        api_url = self.source.extra.get("api_url") or self._default_api_url()
        if not api_url:
            raise ValueError(f"{self.source.id} requires extra.api_url or extra.site_number for Oracle HCM")
        api_url = str(api_url)
        if self._fallback_listing_url() and self._preflight_dns_enabled() and not _host_resolves(api_url):
            return self._fetch_fallback_jobs("dns_resolution_failed")
        try:
            return self._fetch_paginated_jobs(api_url)
        except Exception as exc:
            if classify_fetch_error(exc) == "dns_resolution_failed" and self._fallback_listing_url():
                return self._fetch_fallback_jobs("dns_resolution_failed")
            raise

    def _fetch_paginated_jobs(self, api_url: str) -> list[JobRecord]:
        page_size = _as_int(self.source.extra.get("page_size"), default=25)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total_count: int | None = None
        for page in range(max_pages):
            offset = page * page_size
            page_url = self._page_url(api_url, limit=page_size, offset=offset)
            self.ensure_allowed(page_url)
            payload = self.context.http.get(page_url, headers=self._oracle_headers()).json()
            page_jobs = self.parse_jobs(payload)
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
            total_count = _total_count(payload) or total_count
            if total_count is not None and offset + page_size >= total_count:
                break
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        rows = _rows(payload)
        jobs = []
        for item in rows:
            external_id = item.get("RequisitionNumber") or item.get("Id") or item.get("RequisitionId")
            apply_url = (
                item.get("ExternalApplyUrl")
                or item.get("ApplyURL")
                or self._apply_url_from_template(external_id)
                or _first_link_href(item.get("links"))
                or str(external_id)
            )
            jobs.append(
                build_job(
                    self.source,
                    title=item.get("Title"),
                    external_id=external_id,
                    location=_location(item),
                    department=item.get("Department")
                    or item.get("OrganizationName")
                    or item.get("Organization")
                    or item.get("BusinessUnit")
                    or item.get("LegalEmployer"),
                    employment_type=item.get("ContractType")
                    or item.get("WorkerType")
                    or item.get("JobType")
                    or item.get("WorkplaceType"),
                    posted_at=item.get("PostedDate") or item.get("ExternalPostedStartDate"),
                    closes_at=item.get("ExternalPostedEndDate") or item.get("PostingEndDate"),
                    apply_url=str(apply_url),
                    description=_description(item),
                    raw=self._raw_with_source_notice(item, source_priority="oracle_hcm_ce"),
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = item.get("RequisitionNumber") or item.get("Id") or item.get("RequisitionId")
        if external_id is None:
            return None
        detail_url = self._detail_api_url(str(external_id))
        if detail_url is None:
            return None
        self.ensure_allowed(detail_url)
        return next(iter(self.parse_jobs(self.context.http.get(detail_url, headers=self._oracle_headers()).json())), None)

    def _default_api_url(self) -> str | None:
        site_number = self.source.extra.get("site_number")
        if not site_number:
            return None
        base = self.source.base_url.rstrip("/")
        return f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    def _page_url(self, api_url: str, *, limit: int, offset: int) -> str:
        template = self.source.extra.get("list_url_template")
        site_number = self.source.extra.get("site_number")
        if template:
            return str(template).format(limit=limit, offset=offset, site_number=site_number or "")
        if "finder=findReqs" in api_url:
            if "offset=" in api_url:
                return api_url
            suffix = f",offset={offset}" if offset else ""
            return f"{api_url}{suffix}"
        if not site_number:
            return api_url
        sort_by = self.source.extra.get("sort_by") or "POSTING_DATES_DESC"
        finder = (
            f"findReqs;siteNumber={site_number},facetsList={_FACETS},"
            f"limit={limit},offset={offset},sortBy={sort_by}"
        )
        return (
            f"{api_url}?onlyData=true&expand={quote(_EXPAND, safe=',.')}"
            f"&finder={quote(finder, safe=';,=')}"
        )

    def _detail_api_url(self, external_id: str) -> str | None:
        template = self.source.extra.get("detail_api_url_template")
        if template:
            return str(template).format(job_id=external_id)
        site_number = self.source.extra.get("site_number")
        if not site_number:
            return None
        base = self.source.base_url.rstrip("/")
        return (
            f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?expand=all&onlyData=true&finder=ById;Id={external_id},siteNumber={site_number}"
        )

    def _apply_url_from_template(self, external_id: Any) -> str | None:
        if external_id is None:
            return None
        template = self.source.extra.get("detail_url_template") or self.source.extra.get(
            "apply_url_template"
        )
        if template:
            return str(template).format(job_id=external_id)
        site_number = self.source.extra.get("site_number")
        if not site_number:
            return None
        return (
            f"{self.source.base_url.rstrip('/')}/hcmUI/CandidateExperience/en/sites/"
            f"{site_number}/job/{external_id}"
        )

    def _oracle_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/vnd.oracle.adf.resourceitem+json;charset=utf-8",
            "ora-irc-cx-userid": str(uuid.uuid4()),
            "ora-irc-language": str(self.source.extra.get("language") or "en"),
        }

    def _fallback_listing_url(self) -> str | None:
        value = self.source.extra.get("fallback_listing_url")
        return str(value) if value else None

    def _fallback_parser(self) -> str:
        configured = self.source.extra.get("fallback_parser")
        if configured:
            return str(configured)
        if "unfpa" in self.source.id.lower():
            return "unfpa_official"
        if "undp" in self.source.id.lower():
            return "undp_official"
        return "generic"

    def _preflight_dns_enabled(self) -> bool:
        return _as_bool(self.source.extra.get("oracle_dns_preflight"), default=True)

    def _fetch_fallback_jobs(self, reason: str) -> list[JobRecord]:
        fallback_url = self._fallback_listing_url()
        if not fallback_url:
            return []
        parser = self._fallback_parser()
        if parser == "undp_official":
            html = self.fetch_text(fallback_url)
            return _parse_undp_official_jobs(self.source, html, fallback_url, reason=reason)
        if parser == "unfpa_official":
            return self._fetch_unfpa_fallback_jobs(fallback_url, reason=reason)
        if parser in {"generic", "generic_official"}:
            html = self.fetch_text(fallback_url)
            return _parse_generic_official_jobs(self.source, html, fallback_url, reason=reason)
        raise ValueError(f"Unsupported Oracle HCM fallback_parser={parser!r} for {self.source.id}")

    def _fetch_unfpa_fallback_jobs(self, fallback_url: str, *, reason: str) -> list[JobRecord]:
        max_pages = _as_int(
            self.source.extra.get("fallback_max_pages"),
            default=_as_int(self.source.extra.get("max_pages"), default=10),
        )
        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        for page in range(max_pages):
            page_url = fallback_url if page == 0 else _url_with_page(fallback_url, page)
            html = self.fetch_text(page_url)
            page_jobs = _parse_unfpa_official_jobs(self.source, html, page_url, reason=reason)
            new_count = 0
            for job in page_jobs:
                key = job.identity_key()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                jobs.append(job)
                new_count += 1
            if not page_jobs or new_count == 0:
                break
        return jobs

    def _raw_with_source_notice(
        self,
        item: dict[str, Any],
        *,
        source_priority: str,
        listing_status: str = "full_listing_available",
    ) -> dict[str, Any]:
        raw = dict(item)
        raw.setdefault("source_priority", source_priority)
        raw.setdefault("listing_status", listing_status)
        if self.source.extra.get("fraud_warning"):
            raw.setdefault("fraud_warning", str(self.source.extra["fraud_warning"]))
        return raw


def classify_fetch_error(exc: Exception) -> str:
    text = repr(exc).lower()
    if (
        "name or service not known" in text
        or "nodename nor servname provided" in text
        or "temporary failure in name resolution" in text
        or "failed to resolve" in text
        or "getaddrinfo" in text
    ):
        return "dns_resolution_failed"
    match = _HTTP_STATUS_RE.search(str(exc))
    if match:
        status = int(match.group(1))
        if status in {401, 403}:
            return "auth_or_forbidden"
        if status == 404:
            return "not_found"
        if status == 429:
            return "rate_limited"
        return f"http_{status}"
    return "unknown_error"


def _first_link_href(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("href"):
            return str(item["href"])
    return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        requisitions = item.get("requisitionList")
        if isinstance(requisitions, list):
            rows.extend(req for req in requisitions if isinstance(req, dict))
        elif item.get("Title"):
            rows.append(item)
    return rows


def _total_count(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for item in payload.get("items", []):
        if isinstance(item, dict) and item.get("TotalJobsCount") is not None:
            try:
                return int(item["TotalJobsCount"])
            except (TypeError, ValueError):
                return None
    return None


def _location(item: dict[str, Any]) -> str | None:
    primary = item.get("PrimaryLocation") or item.get("Location")
    locations = [str(primary)] if primary else []
    for key in ("secondaryLocations", "otherWorkLocations"):
        value = item.get(key)
        if not isinstance(value, list):
            continue
        for location in value:
            if isinstance(location, dict):
                text = location.get("Name") or location.get("LocationName") or location.get("PrimaryLocation")
                if text:
                    locations.append(str(text))
            elif location:
                locations.append(str(location))
    return "; ".join(dict.fromkeys(locations)) or None


def _description(item: dict[str, Any]) -> str | None:
    parts = [
        item.get("ShortDescription") or item.get("ShortDescriptionStr"),
        item.get("ExternalDescriptionStr") or item.get("Description"),
        item.get("ExternalResponsibilitiesStr"),
        item.get("ExternalQualificationsStr"),
    ]
    return "\n\n".join(str(part) for part in parts if part)


def _host_resolves(url: str) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return True
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror:
        return False
    return True


def _parse_undp_official_jobs(
    source,
    html: str,
    listing_url: str,
    *,
    reason: str,
) -> list[JobRecord]:
    jobs = []
    for anchor in _extract_anchors(html):
        text = _clean(anchor["text"])
        if not text.startswith("Job Title ") or " Apply by " not in text:
            continue
        fields = _parse_undp_anchor_text(text)
        if not fields:
            continue
        href = urljoin(listing_url, anchor["href"])
        external_id = _external_id_from_url(href)
        jobs.append(
            build_job(
                source,
                title=fields["title"],
                external_id=external_id,
                location=fields.get("location"),
                employment_type=fields.get("post_level"),
                closes_at=_normalize_fallback_date(fields.get("closes_at")),
                apply_url=href,
                source_url=href,
                raw={
                    **fields,
                    "source_priority": "official_agency_listing_page",
                    "listing_status": _OFFICIAL_LISTING_STATUS,
                    "fallback_reason": reason,
                    "fraud_warning": _FRAUD_WARNING,
                    "href": href,
                },
            )
        )
    return jobs


def _parse_undp_anchor_text(text: str) -> dict[str, str] | None:
    title = _between(text, "Job Title ", " Post level ")
    if title is None:
        return None
    post_level = _between(text, " Post level ", " Apply by ") or ""
    closes_at = _between(text, " Apply by ", " Agency ") or ""
    agency = _between(text, " Agency ", " Location ") or ""
    location = text.rsplit(" Location ", 1)[-1] if " Location " in text else ""
    return {
        "title": title,
        "post_level": post_level,
        "closes_at": closes_at,
        "agency": agency,
        "location": location,
    }


def _parse_unfpa_official_jobs(
    source,
    html: str,
    listing_url: str,
    *,
    reason: str,
) -> list[JobRecord]:
    tokens = _current_unfpa_tokens(_HTMLTokenParser.parse(html))
    jobs = []
    for index, token in enumerate(tokens):
        if token["type"] != "a":
            continue
        title = _clean(token["text"])
        if not title or title.lower() in {"apply", "view job"}:
            continue
        closes_at = _field_after(tokens, index, "Closing date")
        location = _field_after(tokens, index, "Location")
        if not closes_at or not location:
            continue
        normalized_closes_at = _normalize_fallback_date(closes_at)
        if _is_expired_closing_date(normalized_closes_at):
            continue
        grade = _field_after(tokens, index, "Staff grade/level")
        contract_type = _field_after(tokens, index, "Contract type")
        detail_url = urljoin(listing_url, token["href"])
        apply_url = _next_anchor_href(tokens, index, "Apply")
        apply_url = urljoin(listing_url, apply_url) if apply_url else detail_url
        external_id = (
            _external_id_from_url(apply_url)
            or _external_id_from_url(detail_url)
            or _slug_from_url(detail_url)
        )
        jobs.append(
            build_job(
                source,
                title=title,
                external_id=external_id,
                location=location,
                employment_type=contract_type or grade,
                closes_at=normalized_closes_at,
                apply_url=apply_url,
                source_url=detail_url,
                raw={
                    "title": title,
                    "closing_date": closes_at,
                    "location": location,
                    "grade": grade,
                    "contract_type": contract_type,
                    "source_priority": "official_agency_listing_page",
                    "listing_status": _OFFICIAL_LISTING_STATUS,
                    "fallback_reason": reason,
                    "fraud_warning": _FRAUD_WARNING,
                    "detail_url": detail_url,
                    "apply_url": apply_url,
                },
            )
        )
    return jobs


def _parse_generic_official_jobs(
    source,
    html: str,
    listing_url: str,
    *,
    reason: str,
) -> list[JobRecord]:
    jobs = []
    seen: set[str] = set()
    for anchor in _extract_anchors(html):
        href = _canonical_oracle_candidate_job_url(urljoin(listing_url, anchor["href"]))
        title = _clean(anchor["text"])
        if not _looks_like_job_anchor(href, title):
            continue
        external_id = _external_id_from_url(href) or _slug_from_url(href)
        key = external_id or href
        if key in seen:
            continue
        seen.add(key)
        jobs.append(
            build_job(
                source,
                title=title,
                external_id=external_id,
                apply_url=href,
                source_url=href,
                raw={
                    "title": title,
                    "href": href,
                    "source_priority": "official_agency_listing_page",
                    "listing_status": _OFFICIAL_LISTING_STATUS,
                    "fallback_reason": reason,
                    "fallback_parser": "generic_official",
                },
            )
        )
    return jobs


def _looks_like_job_anchor(href: str, title: str) -> bool:
    if not title or len(title) < 8:
        return False
    lower_title = title.casefold()
    if lower_title in {
        "read more",
        "learn more",
        "view all",
        "view jobs",
        "job openings",
        "employment",
        "careers",
        "apply now",
    }:
        return False
    parts = urlsplit(href)
    if parts.scheme not in {"http", "https"}:
        return False
    lowered = f"{parts.path}?{parts.query}".casefold()
    return any(
        signal in lowered
        for signal in (
            "job",
            "career",
            "vacanc",
            "position",
            "requisition",
            "opening",
            "employment",
        )
    )


def _canonical_oracle_candidate_job_url(url: str) -> str:
    if "/hcmUI/CandidateExperience/" not in url:
        return url
    return url.replace("/requisitions/job/", "/job/")


def _current_unfpa_tokens(tokens: list[dict[str, str]]) -> list[dict[str, str]]:
    current_seen = False
    start = 0
    for index, token in enumerate(tokens):
        text = _clean(token["text"]).lower()
        if text == "current jobs":
            current_seen = True
        elif current_seen and re.fullmatch(r"\d+\s+results found", text):
            start = index + 1
            break
    end = len(tokens)
    for index in range(start, len(tokens)):
        if _clean(tokens[index]["text"]).lower() == "pagination":
            end = index
            break
    return tokens[start:end]


def _field_after(tokens: list[dict[str, str]], start: int, label: str) -> str | None:
    label_lower = label.lower()
    field_labels = {"closing date", "location", "staff grade/level", "contract type"}
    found = False
    for token in tokens[start + 1 : start + 30]:
        text = _clean(token["text"])
        if not text:
            continue
        lower = text.lower()
        if lower == "pagination":
            return None
        if found:
            if lower in field_labels or lower in {"apply", "view job"}:
                continue
            return text
        if lower == label_lower:
            found = True
    return None


def _next_anchor_href(tokens: list[dict[str, str]], start: int, text: str) -> str | None:
    target = text.lower()
    for token in tokens[start + 1 : start + 30]:
        if token["type"] == "a" and _clean(token["text"]).lower() == target:
            return token.get("href")
    return None


def _url_with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _normalize_fallback_date(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = re.search(r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", text)
    if match:
        return match.group(1)
    match = re.fullmatch(r"([A-Za-z]{3})-(\d{1,2})-(\d{2}|\d{4})", text)
    if match:
        year = match.group(3)
        if len(year) == 2:
            year = f"20{year}"
        return f"{match.group(1)} {int(match.group(2))}, {year}"
    return text


def _between(text: str, start_marker: str, end_marker: str) -> str | None:
    try:
        start = text.index(start_marker) + len(start_marker)
        end = text.index(end_marker, start)
    except ValueError:
        return None
    return text[start:end].strip()


def _external_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/(?:requisitions/)?job/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    query = dict(parse_qsl(urlsplit(url).query))
    return query.get("keyword") or query.get("job_id") or query.get("id")


def _slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return None
    return path.rsplit("/", 1)[-1] or None


def _is_expired_closing_date(value: str | None) -> bool:
    parsed = parse_datetime(value)
    if parsed is None:
        return False
    return parsed.date() < datetime.now(tz=UTC).date()


def _extract_anchors(html: str) -> list[dict[str, str]]:
    return [token for token in _HTMLTokenParser.parse(html) if token["type"] == "a"]


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return _SPACE_RE.sub(" ", unescape(str(value))).strip()


class _HTMLTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    @classmethod
    def parse(cls, html: str) -> list[dict[str, str]]:
        parser = cls()
        parser.feed(html)
        parser.close()
        return parser.tokens

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._anchor_href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._anchor_href = href
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor_href is None:
            return
        text = _clean(" ".join(self._anchor_text))
        if text:
            self.tokens.append({"type": "a", "text": text, "href": self._anchor_href})
        self._anchor_href = None
        self._anchor_text = []

    def handle_data(self, data: str) -> None:
        text = _clean(data)
        if not text:
            return
        if self._anchor_href is not None:
            self._anchor_text.append(text)
        else:
            self.tokens.append({"type": "text", "text": text, "href": ""})
