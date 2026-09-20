"""Oracle Taleo adapter for REST faceted search and legacy HTML pages."""

from __future__ import annotations

import ast
import copy
import html
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jobagg.adapters.base import AdapterContext, JobAdapter, register_adapter
from jobagg.adapters.taleo_public_bindings import public_bindings
from jobagg.models import JobRecord
from jobagg.normalize import build_job, clean_text
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html
from jobagg.vacancy_outcomes import DetailIdentityMismatch, VacancyUnavailable, unavailable_template

_ANCHOR_RE = re.compile(
    r"<a[^>]+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _jobs_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("requisitionList", "requisitions", "jobs", "items", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    for value in payload.values():
        if isinstance(value, dict):
            rows = _jobs_from_payload(value)
            if rows:
                return rows
    return []


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    lower_map = {key.lower(): value for key, value in data.items()}
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
        value = lower_map.get(key.lower())
        if value not in (None, ""):
            return value
    return None


@register_adapter
class TaleoAdapter(JobAdapter):
    family = "taleo"

    def fetch_jobs(self) -> list[JobRecord]:
        if self.source.extra.get("search_api_url"):
            return self._fetch_rest_jobs()
        search_url = self.source.extra.get("search_url") or self.source.base_url
        text = self.fetch_text(str(search_url))
        try:
            return self.parse_jobs(json.loads(text))
        except json.JSONDecodeError:
            return self.parse_jobs_from_html(text)

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        jobs = []
        for item in _jobs_from_payload(payload):
            jobs.append(self.parse_listing_item(item))
        return jobs

    def _fetch_rest_jobs(self) -> list[JobRecord]:
        if _as_bool(self.source.extra.get("enumerate_job_locales"), default=False):
            return self._fetch_multilingual_rest_jobs()
        return self._fetch_rest_jobs_single()

    def _fetch_multilingual_rest_jobs(self) -> list[JobRecord]:
        """Enumerate the advertised posting locales, deduplicating vacancy IDs.

        Taleo's JOB_LOCALE control changes the URL lang parameter. It is not a
        normal selectedValues facet. Counts remain separate because a vacancy
        may be advertised in several languages, and source totals may be stale.
        """
        api = str(self.source.extra["search_api_url"])
        default_locale = dict(parse_qsl(urlsplit(api).query)).get("lang", "en")
        pending = [default_locale]
        by_key: dict[str, JobRecord] = {}
        locales: dict[str, Any] = {}
        max_locales = _as_int(self.source.extra.get("max_job_locales"), default=10)
        while pending:
            locale = pending.pop(0)
            if locale in locales:
                continue
            if len(locales) >= max_locales:
                raise ValueError("Taleo posting-language limit reached before inventory completion")
            extra = {**self.source.extra, "enumerate_job_locales": False, "accept_language": locale.replace("_", "-"), "preserve_detail_timezone": True}
            extra["search_api_url"] = self._language_url(api, locale)
            if extra.get("search_url"):
                extra["search_url"] = self._language_url(str(extra["search_url"]), locale)
            child = type(self)(AdapterContext(replace(self.source, extra=extra), self.context.http, self.context.robots))
            jobs = child._fetch_rest_jobs_single(posting_locale=locale)
            facets = child.listing_language_facets
            for advertised in facets:
                if advertised not in locales and advertised not in pending and advertised != locale:
                    pending.append(advertised)
            locales[locale] = {
                "search_api_url": extra["search_api_url"],
                "observed_ids": sorted(str(j.external_id) for j in jobs),
                "unique_count": len(jobs),
                "reported_total": child.run_diagnostics.total_reported_by_source,
                "pagination_complete": child.run_diagnostics.pagination_complete,
                "pages_fetched": child.run_diagnostics.pages_fetched,
                "advertised_posting_locales": facets,
            }
            for job in jobs:
                row = copy.deepcopy(job.raw["_taleo_locale_listings"][locale])
                existing = by_key.get(job.identity_key())
                if existing is None:
                    by_key[job.identity_key()] = job
                else:
                    existing.raw["_taleo_available_locales"].append(locale)
                    existing.raw["_taleo_locale_listings"][locale] = row
        self.language_inventory = {
            "locales": locales, "distinct_count": len(by_key),
            "locale_totals_must_not_be_summed": True,
            "pagination_complete": all(v["pagination_complete"] is True for v in locales.values()),
        }
        for job in by_key.values():
            job.raw["_taleo_language_inventory"] = copy.deepcopy(self.language_inventory)
        self.run_diagnostics.pages_fetched = sum(v["pages_fetched"] or 0 for v in locales.values())
        self.run_diagnostics.total_reported_by_source = next(iter(locales.values()))["reported_total"] if len(locales) == 1 else None
        self.run_diagnostics.pagination_complete = self.language_inventory["pagination_complete"]
        if not self.run_diagnostics.pagination_complete:
            self.run_diagnostics.scope_validation_status = "incomplete"
        return list(by_key.values())

    @staticmethod
    def _language_url(url: str, locale: str) -> str:
        if not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", locale):
            raise ValueError("Unsupported Taleo public posting-language code")
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "lang"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*query, ("lang", locale)]), parts.fragment))

    def _posting_locale_url(self, url: str, locale: str, external_id: str) -> str:
        """Pin a public posting language without changing its requisition ID."""
        identities = [value for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
                      if key.lower() in {"job", "jobid", "requisition"}]
        if identities != [str(external_id)]:
            raise ValueError("Taleo detail URL identity differs from the public listing")
        return self._language_url(url, locale)

    def _validated_posting_locale(self, item: dict[str, Any], external_id: str) -> str | None:
        locale = item.get("_taleo_posting_locale")
        if locale is None:
            return None
        self._language_url("", str(locale))
        available = item.get("_taleo_available_locales")
        listings = item.get("_taleo_locale_listings")
        row = listings.get(locale) if isinstance(listings, dict) else None
        if (not isinstance(available, list) or locale not in available
                or not isinstance(row, dict)
                or str(self._external_id(self._flatten_item(row))) != str(external_id)):
            raise ValueError("Taleo posting locale lacks matching public listing membership")
        inventory = item.get("_taleo_language_inventory")
        if isinstance(inventory, dict):
            language = inventory.get("locales", {}).get(locale, {})
            if str(external_id) not in language.get("observed_ids", []):
                raise ValueError("Taleo posting locale differs from the public language inventory")
        return str(locale)

    def _bind_listing_locale(self, job: JobRecord, locale: str) -> None:
        original = {"apply_url": job.apply_url, "source_url": job.source_url,
                    "_taleo_detail_url": job.raw.get("_taleo_detail_url")}
        row = copy.deepcopy(job.raw)
        job.apply_url = self._posting_locale_url(job.apply_url, locale, str(job.external_id))
        job.source_url = self._posting_locale_url(job.source_url or original["apply_url"], locale, str(job.external_id))
        job.raw.update(_taleo_posting_locale=locale, _taleo_available_locales=[locale],
                       _taleo_locale_listings={locale: row}, _taleo_original_urls=original,
                       _taleo_detail_url=job.apply_url,
                       _taleo_locale_url_resolution={"posting_locale": locale,
                                                     "basis": "public listing returned in this JOB_LOCALE inventory",
                                                     "public_detail_url": job.apply_url})

    def _fetch_rest_jobs_single(self, *, posting_locale: str | None = None) -> list[JobRecord]:
        search_api_url = str(self.source.extra["search_api_url"])
        payload_template = self.source.extra.get("search_payload") or self._default_search_payload()
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        warmup_search_page = _as_bool(self.source.extra.get("warmup_search_page"), default=True)
        if warmup_search_page and self.source.extra.get("search_url"):
            self.fetch_text(str(self.source.extra["search_url"]))

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        totals: set[int] = set()
        pages_fetched = 0
        terminal = False
        self.listing_language_facets: dict[str, Any] = {}
        for page_no in range(1, max_pages + 1):
            payload = copy.deepcopy(payload_template)
            if isinstance(payload, dict):
                payload["pageNo"] = page_no
            page = self.post_json(search_api_url, payload, headers=self._rest_headers())
            for facet in page.get("facetResults", []) if isinstance(page, dict) else []:
                if facet.get("id") == "JOB_LOCALE":
                    for value in facet.get("facetValueResults", []):
                        locale = str(value.get("id") or "")
                        if re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", locale):
                            self.listing_language_facets[locale] = copy.deepcopy(value)
            pages_fetched += 1
            paging = page.get("pagingData", {}) if isinstance(page, dict) else {}
            raw_total = paging.get("totalCount") if isinstance(paging, dict) else None
            if raw_total is not None and str(raw_total).isdigit():
                totals.add(int(raw_total))
            rows = _jobs_from_payload(page)
            if not rows:
                terminal = True
                break
            page_new = 0
            for item in rows:
                job = self.parse_listing_item(item)
                if posting_locale is not None:
                    self._bind_listing_locale(job, posting_locale)
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
            if page_new == 0:
                break
            if self._is_last_page(page, page_no):
                terminal = True
                break
        expected = next(iter(totals)) if len(totals) == 1 else None
        self.run_diagnostics.pages_fetched = pages_fetched
        self.run_diagnostics.total_reported_by_source = expected
        self.run_diagnostics.pagination_complete = (
            terminal and expected is not None and len(jobs) == expected
        )
        if expected == 0 and not jobs:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = {"total_reported_by_source": 0}
        return jobs

    def parse_listing_item(self, item: dict[str, Any]) -> JobRecord:
        flat = self._flatten_item(item)
        external_id = self._external_id(flat)
        apply_url = (
            _first_value(flat, "url", "jobUrl", "jobDetailUrl", "detailUrl", "applyUrl")
            or self._job_detail_url(str(external_id or ""))
        )
        return build_job(
            self.source,
            title=_first_value(
                flat,
                "title",
                "jobTitle",
                "requisitionTitle",
                "contestTitle",
                "Requisition Title",
                "Job Title",
            ),
            external_id=external_id,
            location=_first_value(flat, "location", "primaryLocation", "Location"),
            department=_first_value(flat, "organization", "department", "Job Field", "Organization"),
            employment_type=_first_value(
                flat,
                "employment_type",
                "employmentType",
                "jobType",
                "schedule",
                "Job Type",
                "Schedule",
            ),
            posted_at=_first_value(flat, "postedDate", "postingDate", "Job Posting", "Posted Date"),
            closes_at=_first_value(
                flat,
                "closingDate",
                "Closing Date",
                "Deadline",
                "Closing Date (Period for Applying) - Internal",
            ),
            apply_url=str(apply_url),
            source_url=str(apply_url),
            description=_first_value(flat, "description", "jobDescription"),
            raw={**item, "_taleo_flat": flat, "_taleo_detail_url": str(apply_url), "_taleo_record_kind": "listing"},
        )

    def parse_jobs_from_html(self, html_text: str) -> list[JobRecord]:
        jobs = []
        for match in _ANCHOR_RE.finditer(html_text):
            href = match.group("href")
            if "jobdetail" not in href.lower() and "job=" not in href.lower():
                continue
            title = _TAG_RE.sub("", match.group("title"))
            external_id_match = re.search(r"(?:job|jobId|requisition)=([^&?#]+)", href, re.IGNORECASE)
            external_id = external_id_match.group(1) if external_id_match else href.rstrip("/").split("/")[-1]
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=external_id,
                    apply_url=href,
                    raw={"href": href, "title": title},
                )
            )
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        detail_url = item.get("_taleo_detail_url")
        expected = self._external_id(self._flatten_item(item))
        if not detail_url:
            if not expected:
                return None
            detail_url = self._job_detail_url(str(expected))
        expected = expected or self._job_id_from_url(str(detail_url))
        locale = self._validated_posting_locale(item, str(expected))
        detail_url = self._with_detail_timezone(str(detail_url))
        if locale:
            detail_url = self._posting_locale_url(detail_url, locale, str(expected))
            if getattr(self, "_active_taleo_locale", None) != locale:
                search = str(self.source.extra.get("search_url") or self.source.base_url)
                self.fetch_text(self._language_url(search, locale))
                self._active_taleo_locale = locale
        html_text = self.fetch_text(str(detail_url))
        if unavailable_template(self.source.id, str(expected), str(detail_url), str(detail_url), html_text):
            raise VacancyUnavailable("Taleo active template explicitly reports unavailable requisition")
        job = self.parse_detail_html(html_text, str(detail_url))
        returned = self._parse_taleo_detail_payload(html_text).get("external_id")
        if (locale and not returned) or (returned and expected and str(returned) != str(expected)):
            raise DetailIdentityMismatch("Taleo returned requisition identity differs from listing")
        job.raw.update(detail_html=html_text, _taleo_listing=copy.deepcopy(item))
        for key in ("_taleo_posting_locale", "_taleo_available_locales", "_taleo_locale_listings", "_taleo_language_inventory", "_taleo_original_urls"):
            if key in item:
                job.raw[key] = copy.deepcopy(item[key])
        if locale:
            job.raw["_taleo_locale_url_resolution"] = {"posting_locale": locale,
                "basis": "public language-inventory membership and matching returned requisition ID",
                "public_detail_url": detail_url, "original_listing_detail_url": item.get("_taleo_detail_url")}
        return job

    def parse_detail_html(self, html_text: str, detail_url: str) -> JobRecord:
        parsed_detail = self._parse_taleo_detail_payload(html_text)
        flat = parsed_detail.get("flat") if isinstance(parsed_detail.get("flat"), dict) else {}
        title = (
            parsed_detail.get("title")
            or self._meta_content(html_text, "og:title")
            or self._title_text(html_text)
        )
        body = parsed_detail.get("description") or self._clean_html_document(html_text)
        external_id = parsed_detail.get("external_id") or self._job_id_from_url(detail_url)
        closes_at = self._extract_labeled_value(
            body,
            "Closing Date",
            "Deadline",
            "Closing Date (Period for Applying) - Internal",
        ) or flat.get("Closing Date")
        posted_at = self._extract_labeled_value(body, "Job Posting", "Posting Date") or flat.get("Job Posting")
        location = (
            self._extract_labeled_value(body, "Primary Location", "Location")
            or flat.get("LOCATION")
        )
        department = (
            self._extract_labeled_value(body, "Organization", "Job Field")
            or flat.get("JOB_FIELD")
            or flat.get("ORGANIZATIONAL_UNIT")
        )
        employment_type = (
            self._extract_labeled_value(body, "Type of Requisition", "Schedule", "Job Type")
            or flat.get("TYPE_OF_REQUISITION")
            or flat.get("JOB_TYPE")
            or flat.get("POSITION_LEVEL_LABEL")
            or flat.get("SCHEDULE")
        )
        job = build_job(
            self.source,
            title=title,
            external_id=external_id,
            location=location,
            department=department,
            employment_type=employment_type,
            posted_at=posted_at,
            closes_at=closes_at,
            apply_url=detail_url,
            source_url=detail_url,
            description=body,
            raw={
                "detail_url": detail_url,
                "_taleo_detail_url": detail_url,
                "_taleo_flat": flat,
            },
        )
        if body:
            job.description = body
        if parsed_detail.get("public_bindings"):
            job.raw["_taleo_public_metadata_resolution"] = {"kind":"paired_public_dom_bindings", "binding":"_taleo_flat._taleo_public_bindings"}
        if all(parsed_detail.get(key) for key in ("external_id", "title", "description")):
            job.raw["_taleo_record_kind"] = "detail"
        values = self._detail_fill_list_values(html_text)
        if job.raw.get("_taleo_record_kind") == "detail":
            # Taleo serializes request-local wall clocks on every provider.
            # An unqualified response cannot establish its UTC offset.
            is_fao = self._is_fao_detail_layout(values) and not parsed_detail.get("public_bindings")
            posting_value = str(values[12] if is_fao else flat.get("Job Posting") or "")
            public_value = str(values[14] if is_fao else flat.get("Closing Date") or "")
            posting = self._localized_taleo_date(posting_value)
            closing = self._localized_taleo_date(public_value)
            query_pairs = parse_qsl(urlsplit(detail_url).query, keep_blank_values=True)
            query = dict(query_pairs)
            timezone_pairs = [(key, value) for key, value in query_pairs if key in {"tz", "tzname"}]
            unambiguous_timezone_pair = len(timezone_pairs) == 2 and {key for key, _ in timezone_pairs} == {"tz", "tzname"}
            zone_name = query.get("tzname")
            resolution = {"url": detail_url, "public_value": public_value, "tzname": zone_name}
            posting_has_clock = bool(re.search(r",\s*\d{1,2}:\d{2}", posting_value))
            local_posting = self._bound_deadline(posting, query) if posting and posting_has_clock and unambiguous_timezone_pair else None
            job.posted_at = local_posting.astimezone(UTC) if local_posting else None
            job.raw["_taleo_posting_time_resolution"] = {
                "url": detail_url, "public_value": posting_value, "tzname": zone_name,
                "kind": "known_instant" if local_posting else ("public_calendar_date_only" if posting and not posting_has_clock else "unknown_timezone"),
            }
            job.closes_at = None
            job.closes_at_local = public_value or None
            job.closes_tz = None
            closing_has_clock = bool(re.search(r",\s*\d{1,2}:\d{2}", public_value))
            local = self._bound_deadline(closing, query) if closing and closing_has_clock and unambiguous_timezone_pair else None
            if local:
                job.closes_at, job.closes_tz = local.astimezone(UTC), zone_name
                resolution["kind"] = "known_instant"
                job.raw["_taleo_deadline_timezone_evidence"] = dict(resolution)
            elif public_value.strip().casefold() in {"continuo", "continu", "ongoing"}:
                resolution["kind"] = "open_ended"
                job.closes_at_local = None
                job.raw["_taleo_deadline_open_ended"] = dict(resolution)
            else:
                resolution["kind"] = "unknown_timezone" if closing else "unparsed"
            job.raw["_taleo_deadline_resolution"] = resolution
        return job

    @staticmethod
    def _bound_deadline(closing: datetime, query: dict[str, str]) -> datetime | None:
        offset = re.fullmatch(r"GMT([+-])(\d{2}):(\d{2})", query.get("tz", ""))
        if not offset or not query.get("tzname"):
            return None
        sign, hours, minutes = offset.groups()
        if int(hours) > 14 or int(minutes) > 59:
            return None
        expected = timedelta(hours=int(hours), minutes=int(minutes)) * (1 if sign == "+" else -1)
        try:
            local = closing.replace(tzinfo=ZoneInfo(query["tzname"]))
        except (ZoneInfoNotFoundError, ValueError):
            return None
        # Conflicting offset/zone pairs and DST-fold ambiguity need review.
        if local.utcoffset() != expected or local.replace(fold=1).utcoffset() != expected:
            return None
        if local.astimezone(UTC).astimezone(local.tzinfo).replace(tzinfo=None) != closing:
            return None
        return local

    @staticmethod
    def _localized_taleo_date(value: str) -> datetime | None:
        months = {
            "jan": 1, "janv": 1, "janvier": 1, "ene": 1, "enero": 1,
            "févr": 2, "février": 2, "feb": 2, "febrero": 2,
            "mars": 3, "mar": 3, "marzo": 3,
            "apr": 4, "avr": 4, "avril": 4, "abr": 4, "abril": 4,
            "mai": 5, "may": 5, "mayo": 5,
            "juin": 6, "jun": 6, "junio": 6,
            "juil": 7, "juillet": 7, "jul": 7, "julio": 7,
            "aug": 8, "août": 8, "ago": 8, "agosto": 8,
            "sept": 9, "septembre": 9, "sep": 9, "septiembre": 9,
            "oct": 10, "octobre": 10, "octubre": 10,
            "nov": 11, "novembre": 11, "noviembre": 11,
            "dec": 12, "déc": 12, "décembre": 12, "dic": 12, "diciembre": 12,
        }
        # Public Taleo providers use day-first named months (FAO/ADB/WIPO),
        # month-first English (WHO), and ISO calendar dates (IAEA).
        value = value.strip()
        clock = r"(?:,\s*(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(AM|PM))?)?"
        match = re.fullmatch(r"(\d{1,2})[/-]([^/-]+)[/-]([0-9]{4})" + clock, value, re.IGNORECASE)
        if match:
            day, name, year, hour, minute, second, period = match.groups()
            month = months.get(name.lower().rstrip('.'))
        else:
            match = re.fullmatch(r"([0-9]{4})-(\d{2})-(\d{2})" + clock, value, re.IGNORECASE)
            if match:
                year, month_value, day, hour, minute, second, period = match.groups()
                month = int(month_value)
            else:
                match = re.fullmatch(r"([A-Za-zÀ-ÿ.]+)\s+(\d{1,2}),\s*([0-9]{4})" + clock, value, re.IGNORECASE)
                if not match:
                    return None
                name, day, year, hour, minute, second, period = match.groups()
                month = months.get(name.lower().rstrip('.'))
        if not month:
            return None
        if period:
            if not 1 <= int(hour) <= 12:
                return None
            hour = str(int(hour) % 12 + (12 if period.upper() == "PM" else 0))
        try:
            return datetime(int(year), month, int(day), int(hour or 0), int(minute or 0), int(second or 0))
        except ValueError:
            return None

    def _default_search_payload(self) -> dict[str, Any]:
        return {
            "multilineEnabled": True,
            "sortingSelection": {
                "sortBySelectionParam": str(self.source.extra.get("sort_by") or "3"),
                "ascendingSortingOrder": str(self.source.extra.get("ascending") or "false"),
            },
            "fieldData": {"fields": {}, "valid": True},
            "filterSelectionParam": {"searchFilterSelections": []},
            "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
            "pageNo": 1,
        }

    def _rest_headers(self) -> dict[str, str]:
        parts = urlsplit(str(self.source.base_url))
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": str(self.source.extra.get("accept_language") or "en"),
            "Referer": str(self.source.extra.get("search_url") or self.source.base_url),
            "X-Requested-With": "XMLHttpRequest",
            "tz": str(self.source.extra.get("tz") or "GMT+00:00"),
            "tzname": str(self.source.extra.get("tzname") or "UTC"),
        }
        if origin:
            headers["Origin"] = origin
        return headers

    def _flatten_item(self, item: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in item.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                flat[key] = value
            elif isinstance(value, dict) and "value" in value:
                flat[key] = value.get("value")

        for container_key in ("column", "columns", "fields", "fieldData"):
            values = item.get(container_key)
            if not isinstance(values, list):
                continue
            for column in values:
                if not isinstance(column, dict):
                    continue
                value = (
                    column.get("value")
                    or column.get("formattedValue")
                    or column.get("text")
                    or column.get("displayValue")
                )
                for key_name in ("name", "label", "id", "field", "key"):
                    key = clean_text(column.get(key_name))
                    if key and value not in (None, ""):
                        flat[key] = self._normalize_column_value(value)

        columns = item.get("column")
        if isinstance(columns, list):
            normalized_columns = [self._normalize_column_value(value) for value in columns]
            linked_column = _as_int(item.get("linkedColumn"), default=-1)
            if 0 <= linked_column < len(normalized_columns):
                flat.setdefault("title", normalized_columns[linked_column])

            column_fields = self.source.extra.get("column_fields") or []
            if isinstance(column_fields, list):
                for index, field_name in enumerate(column_fields):
                    if index >= len(normalized_columns):
                        break
                    if field_name:
                        flat[str(field_name)] = normalized_columns[index]

            location_indexes = item.get("locationsColumns")
            if isinstance(location_indexes, list):
                locations = [
                    normalized_columns[index]
                    for index in location_indexes
                    if isinstance(index, int) and index < len(normalized_columns)
                ]
                if locations:
                    flat.setdefault("location", "; ".join(locations))

        return flat

    def _normalize_column_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return value
            if isinstance(parsed, list):
                return "; ".join(str(item) for item in parsed)
        return value

    def _external_id(self, flat: dict[str, Any]) -> Any:
        external_id = _first_value(
            flat,
            "contestNo",
            "jobNumber",
            "requisitionNumber",
            "requisitionId",
            "jobId",
            "id",
            "Job Number",
            "Req ID",
            "Requisition Number",
        )
        if external_id:
            return external_id
        for key, value in flat.items():
            if "number" in key.lower() or "requisition" in key.lower():
                if value not in (None, ""):
                    return value
        return None

    def _job_detail_url(self, job_id: str) -> str:
        template = self.source.extra.get("detail_url_template")
        if template:
            result = str(template).format(job_id=job_id, job_id_url=quote(job_id, safe=""))
            return self._with_detail_timezone(result)
        if job_id.startswith(("http://", "https://")):
            return job_id
        search_url = str(self.source.extra.get("search_url") or self.source.base_url)
        if "jobsearch.ftl" in search_url:
            base = search_url.replace("jobsearch.ftl", "jobdetail.ftl")
            separator = "&" if "?" in base else "?"
            return f"{base}{separator}job={quote(job_id, safe='')}"
        return urljoin(f"{self.source.base_url.rstrip('/')}/", f"jobdetail.ftl?job={quote(job_id, safe='')}")

    def _with_detail_timezone(self, url: str) -> str:
        if (not any(_as_bool(self.source.extra.get(key), default=False) for key in ("preserve_detail_timezone", "enumerate_job_locales"))
                and not any(self.source.extra.get(key) for key in ("tz", "tzname"))):
            return url
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        present = [(key, value) for key, value in pairs if key in {"tz", "tzname"}]
        if present:
            # Never create a mixed pair by filling one missing parameter.
            if len(present) != 2 or {key for key, _ in present} != {"tz", "tzname"} or not all(value for _, value in present):
                raise ValueError("Taleo detail URL has an incomplete or ambiguous timezone pair")
            return url
        configured = [(key, str(self.source.extra[key])) for key in ("tz", "tzname") if self.source.extra.get(key)]
        if len(configured) == 1:
            raise ValueError("Taleo detail timezone configuration requires both tz and tzname")
        if not configured:
            return url
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*pairs, *configured]), parts.fragment))

    def _job_id_from_url(self, detail_url: str) -> str | None:
        query = dict(parse_qsl(urlsplit(detail_url).query, keep_blank_values=True))
        return query.get("job")

    def _is_last_page(self, payload: Any, page_no: int) -> bool:
        if not isinstance(payload, dict):
            return True
        paging = payload.get("pagingData") if isinstance(payload.get("pagingData"), dict) else payload
        total = paging.get("totalCount")
        size = paging.get("pageSize")
        if total is not None and size is not None and str(total).isdigit() and str(size).isdigit() and int(size) > 0:
            return page_no * int(size) >= int(total)
        total_pages = (
            paging.get("numberOfPages")
            or paging.get("totalPages")
            or paging.get("pageCount")
            or paging.get("lastPage")
        )
        if total_pages is not None:
            return page_no >= _as_int(total_pages, default=page_no)
        current = paging.get("pageNo") or paging.get("pageNumber")
        if current is not None and not _jobs_from_payload(payload):
            return True
        return False

    def _parse_taleo_detail_payload(self, html_text: str) -> dict[str, Any]:
        values = self._detail_fill_list_values(html_text)
        if not values:
            return {}

        binding = public_bindings(html_text, values)
        if binding is not None:
            return self._parse_public_bound_fields(binding)

        if self._is_fao_detail_layout(values):
            title = clean_text(unquote(values[11])) if len(values) > 11 else None
            external_id = clean_text(unquote(values[10])) if len(values) > 10 else None
        else:
            title = clean_text(unquote(values[9])) if len(values) > 9 else None
            external_id = clean_text(unquote(values[10])) if len(values) > 10 else None
        description = self._detail_description(values)
        flat = self._detail_flat_values(values)
        if external_id:
            flat.setdefault("Job Number", external_id)
        if title:
            flat.setdefault("Requisition Title", title)
        return {
            "title": title,
            "external_id": external_id,
            "description": description,
            "flat": flat,
        }

    def _parse_public_bound_fields(self, binding: dict[str, Any]) -> dict[str, Any]:
        semantic_values: dict[str, str] = {}
        flat: dict[str, Any] = {"_taleo_parser": "paired_public_dom_bindings"}
        body_parts, body_seen = [], set()
        for field in binding['visible_fields']:
            encoded = field['encoded_value']
            decoded = unquote(encoded).lstrip('!*')
            value = self._clean_detail_html_fragment(decoded) if '<' in decoded else clean_text(decoded)
            field['public_text'] = value
            if not value:
                continue
            semantic, label = field['semantic'], field['public_label']
            if semantic in semantic_values and semantic_values[semantic] != value:
                raise ValueError('Conflicting Taleo public values for ' + semantic)
            semantic_values[semantic] = value
            if label:
                if label in flat and flat[label] != value:
                    raise ValueError('Conflicting Taleo public field label')
                flat[label] = value
            part = (label + '\n' if label else '') + value
            fingerprint = re.sub(r'\s+', ' ', part).strip()
            if fingerprint not in body_seen:
                body_parts.append(part)
                body_seen.add(fingerprint)
        aliases = {'title':'Requisition Title', 'contestnumber':'Job Number',
                   'postingdate':'Job Posting', 'unpostingdate':'Closing Date',
                   'primarylocation':'LOCATION', 'otherlocations':'OTHER_LOCATIONS',
                   'organization':'ORGANIZATIONAL_UNIT', 'jobschedule':'SCHEDULE',
                   'jobtype':'JOB_TYPE', 'jobfield':'JOB_FIELD'}
        for semantic, alias in aliases.items():
            value = semantic_values.get('reqlistitem.' + semantic)
            if value:
                flat[alias] = value
        public_aliases = {'Grade':'JOB_LEVEL', 'Grade Level':'JOB_LEVEL', 'Position Level':'JOB_LEVEL',
                          'Type of Requisition':'TYPE_OF_REQUISITION', 'Organizational Unit':'ORGANIZATIONAL_UNIT',
                          'Department':'JOB_FIELD', 'Staff Category':'POSITION_LEVEL_LABEL',
                          'Contract Duration':'CONTRACT_DURATION', 'Contract Duration (Years, Months, Days)':'CONTRACT_DURATION',
                          'Contract Type':'JOB_TYPE', 'Contractual Arrangement':'JOB_TYPE', 'Post Number':'POST_NUMBER'}
        for label, alias in public_aliases.items():
            if flat.get(label):
                flat.setdefault(alias, flat[label])
        # WIPO's public department line has no printed label; its exact bound
        # semantic is verified against the current official browser header.
        if self.source.id == 'wipo_taleo':
            value = semantic_values.get('reqlistitem.G170205120713')
            if value:
                flat['ORGANIZATIONAL_UNIT'] = value
        if self.source.id == 'fao_taleo':
            # These source semantic identities are language independent; their
            # current English/Spanish/French public labels remain in evidence.
            for semantic, alias in {'G12005120163':'ORGANIZATIONAL_UNIT',
                                    'G18305120163':'TYPE_OF_REQUISITION', 'G23705120163':'JOB_LEVEL',
                                    'G61905020205':'CONTRACT_DURATION', 'G9905120163':'POST_NUMBER',
                                    'G61805020205':'CLASSIFICATION_CODE'}.items():
                value = semantic_values.get('reqlistitem.' + semantic)
                if value:
                    flat[alias] = value
        flat['_taleo_public_bindings'] = binding
        title = semantic_values.get('reqlistitem.title')
        identity = semantic_values.get('reqlistitem.contestnumber')
        if not title or not identity or not semantic_values.get('reqlistitem.description'):
            raise ValueError('Public Taleo title, identity or main description is empty')
        return {'title':title, 'external_id':identity,
                'description':'\n\n'.join(body_parts), 'flat':flat,
                'public_bindings':binding}

    def _detail_fill_list_values(self, html_text: str) -> list[str]:
        match = re.search(
            r"api\.fillList\("
            r"['\"]requisitionDescriptionInterface['\"]\s*,\s*"
            r"['\"]descRequisition['\"]\s*,\s*\[(.*?)\]\s*\);",
            html_text,
            re.DOTALL,
        )
        if not match:
            return []
        try:
            values = ast.literal_eval(f"[{match.group(1)}]")
        except (SyntaxError, ValueError):
            return []
        return [str(value) for value in values]

    def _detail_description(self, values: list[str]) -> str | None:
        parts: list[str] = []
        seen: set[str] = set()
        for value in values:
            if "%3C" not in value and not value.lstrip().startswith("<"):
                continue
            decoded_html = unquote(value).lstrip("!*")
            text = self._clean_detail_html_fragment(decoded_html)
            if not text:
                continue
            fingerprint = re.sub(r"\s+", " ", text).strip()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            parts.append(text)
        return "\n\n".join(parts) if parts else None

    def _detail_flat_values(self, values: list[str]) -> dict[str, Any]:
        if self._is_fao_detail_layout(values):
            return self._fao_detail_flat_values(values)

        flat: dict[str, Any] = {"_taleo_parser": "requisitionDescriptionInterface.fillList"}
        grade_index, grade_value = self._adb_position_level(values)
        if grade_value:
            flat["JOB_LEVEL"] = grade_value
            flat["Position Level"] = grade_value
            label = self._nearest_previous_nonempty(values, grade_index, exclude={grade_value})
            if label:
                flat["POSITION_LEVEL_LABEL"] = label
                flat["STAFF_CATEGORY"] = label
                flat["Staff Category"] = label

        metadata = self._metadata_after_descriptions(values)
        if metadata:
            flat["LOCATION"] = metadata[0]
            flat["Primary Location"] = metadata[0]
        if len(metadata) > 1:
            flat["JOB_FIELD"] = metadata[1]
            flat["Department"] = metadata[1]
        if len(metadata) > 2:
            flat["ORGANIZATION"] = metadata[2]
            flat["Division"] = metadata[2]
        if (
            len(metadata) > 3
            and metadata[3] != flat.get("STAFF_CATEGORY")
            and not self._looks_like_adb_position_level(metadata[3])
        ):
            flat["UNIT"] = metadata[3]
            flat["Unit"] = metadata[3]
        if len(metadata) > 3:
            flat.setdefault("STAFF_CATEGORY", metadata[3])
            flat.setdefault("Staff Category", metadata[3])
        posting_date = self._posting_date_from_metadata(metadata)
        if posting_date:
            flat["Job Posting"] = posting_date
        closing_date = self._closing_date_from_metadata(metadata)
        if closing_date:
            flat["Closing Date"] = closing_date
            flat["Closing Date (Period for Applying) - Internal"] = closing_date
        return flat

    def _is_fao_detail_layout(self, values: list[str]) -> bool:
        if self.source.id != "fao_taleo" and "jobs.fao.org" not in str(self.source.base_url):
            return False
        return len(values) > 24 and clean_text(values[10]) and clean_text(values[11])

    def _fao_detail_flat_values(self, values: list[str]) -> dict[str, Any]:
        flat: dict[str, Any] = {
            "_taleo_parser": "requisitionDescriptionInterface.fillList",
            "_taleo_detail_layout": "fao",
        }

        def value_at(index: int) -> str | None:
            if index >= len(values):
                return None
            text = clean_text(unquote(values[index]))
            return text or None

        def set_if(key: str, value: str | None) -> None:
            if value not in (None, ""):
                flat[key] = value

        set_if("Job Number", value_at(10))
        set_if("Requisition Title", value_at(11))
        set_if("Job Posting", value_at(12))
        set_if("Closing Date", value_at(14))
        set_if("ORGANIZATIONAL_UNIT", value_at(16))
        set_if("Organizational Unit", value_at(16))
        set_if("JOB_CATEGORY", value_at(18))
        set_if("Job Category", value_at(18))

        requisition_type = value_at(20)
        set_if("TYPE_OF_REQUISITION", requisition_type)
        set_if("Type of Requisition", requisition_type)
        set_if("JOB_TYPE", requisition_type)

        grade_level = value_at(22)
        if grade_level and grade_level.upper() not in {"N/A", "NA"}:
            set_if("JOB_LEVEL", grade_level)
            set_if("Grade Level", grade_level)

        set_if("LOCATION", value_at(24))
        set_if("Primary Location", value_at(24))
        set_if("CONTRACT_DURATION", value_at(26))
        set_if("Contract Duration", value_at(26))
        post_number = value_at(28)
        if post_number and post_number.upper() not in {"N/A", "NA"}:
            set_if("POST_NUMBER", post_number)
            set_if("Post Number", post_number)
        set_if("CLASSIFICATION_CODE", value_at(30))
        return flat

    def _clean_detail_html_fragment(self, html_text: str) -> str | None:
        text = html.unescape(html_text)
        # Drop executable/presentation bodies before their tags are removed;
        # otherwise CSS/JavaScript becomes indistinguishable from job prose.
        text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", text, flags=re.I | re.S)
        text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
        text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
        text = re.sub(r"(?i)</\s*li\s*>", "\n", text)
        text = re.sub(r"(?i)</\s*(p|div|h[1-6]|ul|ol|table|tr)\s*>", "\n\n", text)
        text = re.sub(r"(?i)<\s*(p|div|h[1-6]|ul|ol|table|tr)\b[^>]*>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\u00a0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text or None

    def _adb_position_level(self, values: list[str]) -> tuple[int, str | None]:
        for index, value in enumerate(values):
            text = clean_text(value)
            if text and self._looks_like_adb_position_level(text):
                return index, re.sub(r"\s|-", "", text).upper()
        return -1, None

    def _looks_like_adb_position_level(self, value: str) -> bool:
        pattern = re.compile(r"^(?:TI|TL|IS|NS|AS|M)\s*-?\s*\d{1,2}$", re.IGNORECASE)
        return bool(pattern.fullmatch(value))

    def _metadata_after_descriptions(self, values: list[str]) -> list[str]:
        last_description_index = -1
        for index, value in enumerate(values):
            if "%3C" in value or value.lstrip().startswith("<"):
                last_description_index = index
        metadata: list[str] = []
        for value in values[last_description_index + 1 :]:
            text = clean_text(unquote(value))
            if not text or text in metadata:
                continue
            if text.startswith("Submission for the position"):
                break
            metadata.append(text)
        return metadata

    def _closing_date_from_metadata(self, metadata: list[str]) -> str | None:
        date_pattern = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4},")
        for index, value in enumerate(metadata):
            if value.casefold() == "ongoing":
                for candidate in metadata[index + 1 :]:
                    if date_pattern.match(candidate):
                        return candidate
        for value in reversed(metadata):
            if date_pattern.match(value):
                return value
        return None

    def _posting_date_from_metadata(self, metadata: list[str]) -> str | None:
        date_pattern = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4},")
        for index, value in enumerate(metadata):
            if value.casefold() == "ongoing":
                for candidate in reversed(metadata[:index]):
                    if date_pattern.match(candidate):
                        return candidate
        for value in metadata:
            if date_pattern.match(value):
                return value
        return None

    def _nearest_previous_nonempty(
        self,
        values: list[str],
        index: int,
        *,
        exclude: set[str],
    ) -> str | None:
        normalized_exclude = {item.casefold() for item in exclude}
        for value in reversed(values[:index]):
            text = clean_text(value)
            if text and text.casefold() not in normalized_exclude:
                return text
        return None

    def _meta_content(self, html_text: str, name: str) -> str | None:
        pattern = re.compile(
            rf"<meta[^>]+(?:name|property)=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']+)",
            re.IGNORECASE,
        )
        match = pattern.search(html_text)
        return html.unescape(match.group(1)).strip() if match else None

    def _title_text(self, html_text: str) -> str | None:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        return clean_text(match.group(1)) if match else None

    def _clean_html_document(self, html_text: str) -> str | None:
        return clean_html(html_text)

    def _extract_labeled_value(self, text: str | None, *labels: str) -> str | None:
        if not text:
            return None
        next_labels = (
            "Primary Location",
            "Other Locations",
            "Organization",
            "Schedule",
            "Job Posting",
            "Closing Date",
            "Deadline",
            "Refer ",
        )
        for label in labels:
            pattern = re.compile(
                rf"{re.escape(label)}(?:\\s*\\([^)]*\\))?\\s*[:\\-]\\s*(.+?)(?={'|'.join(map(re.escape, next_labels))}|$)",
                re.IGNORECASE,
            )
            match = pattern.search(text)
            if match:
                value = clean_text(match.group(1).strip(" :;-"))
                if value:
                    return value
        return None
