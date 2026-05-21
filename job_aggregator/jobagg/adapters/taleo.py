"""Oracle Taleo adapter for REST faceted search and legacy HTML pages."""

from __future__ import annotations

import ast
import copy
import html
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job, clean_text
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html

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
        search_api_url = str(self.source.extra["search_api_url"])
        payload_template = self.source.extra.get("search_payload") or self._default_search_payload()
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        warmup_search_page = _as_bool(self.source.extra.get("warmup_search_page"), default=True)
        if warmup_search_page and self.source.extra.get("search_url"):
            self.fetch_text(str(self.source.extra["search_url"]))

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        for page_no in range(1, max_pages + 1):
            payload = copy.deepcopy(payload_template)
            if isinstance(payload, dict):
                payload["pageNo"] = page_no
            page = self.post_json(search_api_url, payload, headers=self._rest_headers())
            rows = _jobs_from_payload(page)
            if not rows:
                break
            page_new = 0
            for item in rows:
                job = self.parse_listing_item(item)
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
                break
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
            raw={**item, "_taleo_flat": flat, "_taleo_detail_url": str(apply_url)},
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
        if not detail_url:
            flat = self._flatten_item(item)
            external_id = self._external_id(flat)
            if not external_id:
                return None
            detail_url = self._job_detail_url(str(external_id))
        html_text = self.fetch_text(str(detail_url))
        return self.parse_detail_html(html_text, str(detail_url))

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
        location = (
            self._extract_labeled_value(body, "Primary Location", "Location")
            or flat.get("LOCATION")
        )
        department = (
            self._extract_labeled_value(body, "Organization", "Job Field")
            or flat.get("JOB_FIELD")
        )
        employment_type = (
            self._extract_labeled_value(body, "Schedule", "Job Type")
            or flat.get("POSITION_LEVEL_LABEL")
        )
        job = build_job(
            self.source,
            title=title,
            external_id=external_id,
            location=location,
            department=department,
            employment_type=employment_type,
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
        return job

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
            return str(template).format(job_id=job_id, job_id_url=quote(job_id, safe=""))
        if job_id.startswith(("http://", "https://")):
            return job_id
        search_url = str(self.source.extra.get("search_url") or self.source.base_url)
        if "jobsearch.ftl" in search_url:
            base = search_url.replace("jobsearch.ftl", "jobdetail.ftl")
            separator = "&" if "?" in base else "?"
            return f"{base}{separator}job={quote(job_id, safe='')}"
        return urljoin(f"{self.source.base_url.rstrip('/')}/", f"jobdetail.ftl?job={quote(job_id, safe='')}")

    def _job_id_from_url(self, detail_url: str) -> str | None:
        query = dict(parse_qsl(urlsplit(detail_url).query, keep_blank_values=True))
        return query.get("job")

    def _is_last_page(self, payload: Any, page_no: int) -> bool:
        if not isinstance(payload, dict):
            return True
        paging = payload.get("pagingData") if isinstance(payload.get("pagingData"), dict) else payload
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

        title = clean_text(values[9]) if len(values) > 9 else None
        external_id = clean_text(values[10]) if len(values) > 10 else None
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

    def _clean_detail_html_fragment(self, html_text: str) -> str | None:
        text = html.unescape(html_text)
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
        pattern = re.compile(r"^(?:TI|TL|IS|NS|AS)\s*-?\s*\d{1,2}$", re.IGNORECASE)
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
