"""UNV Unified Volunteering Platform adapter."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.adapters.unv_public import (display_config, explicit_deadline_conflicts,
                                       public_deadline, render_public, select_translations)
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int


@register_adapter
class UNVAdapter(JobAdapter):
    family = "unv"

    def fetch_jobs(self) -> list[JobRecord]:
        api_url = self.source.extra.get("api_url")
        if not api_url:
            raise ValueError(f"{self.source.id} requires extra.api_url for UNV")
        page_size = _as_int(self.source.extra.get("page_size"), default=10)
        max_pages = _as_int(self.source.extra.get("max_pages"), default=25)
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=False)
        base_payload = copy.deepcopy(self.source.extra.get("search_payload") or {})
        base_payload.setdefault("take", page_size)

        jobs: list[JobRecord] = []
        seen_keys: set[str] = set()
        total: int | None = None
        observed_totals: set[int] = set()
        pages = 0
        for page in range(max_pages):
            payload = copy.deepcopy(base_payload)
            payload["take"] = page_size
            payload["skip"] = page * page_size
            response_payload = self.post_json(str(api_url), payload, headers=self._headers())
            pages += 1
            reported = _total(response_payload)
            if reported is not None:
                observed_totals.add(reported)
                total = max(observed_totals)
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
            if total is not None and len(jobs) >= total:
                break
        self.run_diagnostics.pages_fetched = pages
        self.run_diagnostics.total_reported_by_source = total
        self.run_diagnostics.pagination_complete = len(observed_totals) == 1 and len(jobs) == total
        if not jobs and observed_totals == {0}:
            self.run_diagnostics.health_status = "ok_empty"
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = {"total_reported_by_source": 0}
        return jobs

    def parse_jobs(self, payload: Any) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        for item in _rows(payload):
            item = copy.deepcopy(item)
            external_id = item.get("id") or item.get("doaRequestNo")
            description, rendering = render_public(item)
            closing, resolution = public_deadline(item.get('sourcingEndDate'))
            item['_unv_public_text_verification'] = rendering
            item['_jobagg_main_text_verification'] = rendering
            item['_unv_deadline_resolution'] = resolution
            item['_unv_public_source_conflicts'] = explicit_deadline_conflicts(item, closing)
            detail_payload = isinstance(payload, dict) and isinstance(payload.get('value'), dict) and payload['value'].get('name')
            item['_unv_record_kind'] = 'detail' if detail_payload else 'listing'
            job = build_job(
                    self.source,
                    title=item.get("name"),
                    external_id=external_id,
                    location=_label(item.get("country")),
                    department=_host_entity(item),
                    employment_type=_label(item.get("volunteerType"))
                    or _label(item.get("workArrangement"))
                    or _label(item.get("categoryName")),
                    posted_at=item.get("publishDate"),
                    closes_at=closing,
                    apply_url=self._apply_url(external_id),
                    source_url=self._apply_url(external_id),
                    description=description,
                    raw=item,
                )
            job.closes_at_local = closing.replace(tzinfo=None).isoformat() if closing else None
            job.closes_tz = 'UTC' if closing else None
            if description:
                job.description = description
            jobs.append(job)
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        external_id = item.get("id") or item.get("doaRequestNo")
        detail_url = self._detail_api_url(external_id)
        if not detail_url:
            return None
        response = self.fetch_json(detail_url)
        items = _rows(response)
        if len(items) != 1:
            raise ValueError('UNV detail response is not a single public assignment')
        detail = copy.deepcopy(items[0])
        returned = detail.get('id') or detail.get('doaRequestNo')
        if str(returned) != str(external_id):
            raise ValueError("UNV detail response identity does not match requested vacancy")
        if _as_bool(self.source.extra.get('public_rendering'), default=False):
            self._fetch_public_support(detail)
        job = self.parse_jobs({'value': detail})[0]
        if _as_bool(self.source.extra.get('public_rendering'), default=False) and not job.raw['_unv_public_text_verification']['complete']:
            raise ValueError('UNV full public assignment fields are incomplete: ' + ', '.join(job.raw['_unv_public_text_verification']['missing']))
        return job

    def _fetch_public_support(self, item: dict[str, Any]) -> None:
        """Fetch the same anonymous supporting data used by the public UI."""
        provenance = {}

        def fetch(name: str, url: str) -> dict[str, Any]:
            started = datetime.now(UTC).isoformat()
            value = self.fetch_json(url)
            if not isinstance(value, dict):
                raise ValueError('Invalid UNV public support response: ' + name)
            provenance[name] = {'url': url, 'started_at': started, 'finished_at': datetime.now(UTC).isoformat(),
                                'response_object_sha256': hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                                'hash_scope': 'decoded JSON object, not original HTTP bytes'}
            return value

        if not hasattr(self, '_unv_guest_context'):
            guest_url = str(self.source.extra['public_guest_profile_url'])
            translation_url = str(self.source.extra['public_translation_url'])
            settings_url = str(self.source.extra['public_settings_url'])
            guest = fetch('guest_profile', guest_url)
            translations = fetch('english_translations', translation_url)
            started = datetime.now(UTC).isoformat()
            settings = self.fetch_text(settings_url)
            flags = re.findall(r'["\']?useCategoryConfiguration["\']?\s*[:=]\s*(true|false)\b', settings)
            if len(flags) != 1:
                raise ValueError('Missing or ambiguous UNV public category-configuration setting')
            provenance['public_settings'] = {'url': settings_url, 'started_at': started,
                'finished_at': datetime.now(UTC).isoformat(), 'decoded_text_sha256': hashlib.sha256(settings.encode()).hexdigest()}
            self._unv_guest_context = {'translations': select_translations(translations),
                                       'display_config': display_config(guest),
                                       'category_configuration_enabled': flags[0] == 'true', 'category_sections': None}
            self._unv_guest_provenance = copy.deepcopy(provenance)
        context = copy.deepcopy(self._unv_guest_context)
        provenance.update(copy.deepcopy(self._unv_guest_provenance))
        category = item.get('volunteersCategoryDetails') or {}
        category_code = code_value(category.get('volunteersCategory'))
        if item.get('isOnsite') is True:
            if not category_code or not re.fullmatch(r'[A-Z0-9_]+', category_code):
                raise ValueError('Missing or invalid UNV public category code')
            identity = item.get('id') or item.get('doaRequestNo')
            if not str(identity).isdigit():
                raise ValueError('Invalid UNV public assignment ID')
            duty_url = str(self.source.extra['public_duty_station_url_template']).format(job_id=identity)
            duty = fetch('duty_stations', duty_url)
            if duty.get('isSuccess') is not True or not isinstance(duty.get('value'), dict):
                raise ValueError('UNV duty-station request was not successful')
            item['_unv_duty_station_response'] = duty['value'].get('dutyStationResponse')
            cache = getattr(self, '_unv_eligibility_cache', {})
            if category_code not in cache:
                category_url = str(self.source.extra['public_eligibility_url_template']).format(category_code=category_code)
                eligibility = fetch('category_eligibility', category_url)
                if not isinstance(eligibility.get('values'), list):
                    raise ValueError('Invalid UNV public eligibility response')
                cache[category_code] = eligibility['values'], copy.deepcopy(provenance['category_eligibility'])
                self._unv_eligibility_cache = cache
            item['_unv_eligibility_criteria'], provenance['category_eligibility'] = copy.deepcopy(cache[category_code])
        if context['category_configuration_enabled'] and category_code:
            status = code_value(item.get('status'))
            entity = 'doa,doaCandidate' if status in {'DOA_RECRUITED', 'DOA_SOURCING'} else 'doa'
            cache = getattr(self, '_unv_category_sections_cache', {})
            if (category_code, entity) not in cache:
                url = str(self.source.extra['public_category_sections_url'])
                request = {'volunteerCategoryCode': category_code, 'entityName': entity}
                started = datetime.now(UTC).isoformat()
                response = self.post_json(url, request, headers=self._headers())
                if not isinstance(response, dict) or not isinstance(response.get('sections'), list):
                    raise ValueError('Invalid UNV public category-section response')
                cache[category_code, entity] = response['sections'], {'url': url, 'method': 'POST', 'request': request,
                    'started_at': started, 'finished_at': datetime.now(UTC).isoformat(),
                    'response_object_sha256': hashlib.sha256(json.dumps(response, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                self._unv_category_sections_cache = cache
            context['category_sections'], provenance['category_sections'] = copy.deepcopy(cache[category_code, entity])
        elif context['category_configuration_enabled']:
            # Public UI has no category dispatch when an online assignment has
            # no category. The initial category reducer has no sections filter.
            context['category_configuration_enabled'] = False
            provenance['category_sections'] = {'basis': 'public UI dispatch requires an explicit category code; no category on this assignment'}
        item['_unv_public_render_context'] = context
        item['_unv_public_field_provenance'] = provenance

    def _detail_api_url(self, external_id: Any) -> str | None:
        if external_id is None:
            return None
        template = self.source.extra.get("detail_api_url_template")
        if template:
            return str(template).format(job_id=external_id)
        return f"{self.source.base_url.rstrip('/')}/api/doa/doa/{external_id}"

    def _apply_url(self, external_id: Any) -> str:
        template = self.source.extra.get("detail_url_template") or self.source.extra.get(
            "apply_url_template"
        )
        if template and external_id is not None:
            return str(template).format(job_id=external_id)
        return self.source.base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Referer": self.source.base_url,
        }


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    if isinstance(value, dict):
        result = value.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if value.get("name"):
            return [value]
    for key in ("result", "results", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def _total(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    total = value.get("total") if isinstance(value, dict) else payload.get("total")
    try:
        return int(total)
    except (TypeError, ValueError):
        return None


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("label") or value.get("shortDescription") or value.get("longDescription")
    if value:
        return str(value)
    return None


def _host_entity(item: dict[str, Any]) -> str | None:
    host = item.get("hostEntity")
    if isinstance(host, dict):
        return host.get("name") or _label(host.get("institution"))
    return _label(host)


def code_value(value: Any) -> str | None:
    return (value.get('value') or {}).get('code') if isinstance(value, dict) else None


def _description(item: dict[str, Any]) -> str | None:
    parts = [
        item.get("organizationMission"),
        item.get("context"),
        item.get("taskDescription"),
        item.get("requiredSkillExperience"),
        item.get("competency"),
        item.get("additionalEligibilityCriteria"),
        item.get("accessibilityComment"),
        item.get("livingConditions"),
    ]
    return "\n\n".join(str(part) for part in parts if part)
