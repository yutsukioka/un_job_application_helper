"""Independent, explicit enumeration contracts; adapter success is not a census."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from jobagg.models import OrganizationSource


def source_capability(source: OrganizationSource) -> dict:
    family = source.adapter or source.ats_family
    mode = "public_html"
    detail = "Public job-specific HTML; source parser coverage requires verification."
    gaps = ["Independent enumeration contract not implemented for this source."]
    if family == "workday":
        mode = "json_api"
        detail = "CXS jobPostingInfo details, including repaired source-specific public fields."
        gaps = []
    elif family == "oracle_hcm":
        mode = "json_api"
        detail = "ById detail is mandatory; listing teasers are insufficient. Empty full details remain blocked."
        gaps = []
    elif family == "unv":
        mode = "json_api_and_public_configuration"
        detail = "Assignment plus public guest/configuration/translation/category/duty data are required."
    elif family == "smartrecruiters":
        mode = "json_api"
        detail = "Full posting detail and all public language variants; unique jobs differ from posting rows."
        gaps = []
    elif family == "csod":
        mode = "json_api_then_public_html"
        detail = "World Bank API summaries omit public Selection Criteria; actual public JobPosting required."
    elif source.id in {"icc_successfactors_legacy", "afdb_successfactors_legacy"}:
        mode = "xml_then_public_html"
        detail = (
            "Legacy XML templates are insufficient; resolved public notice and headers required."
        )
    elif family == "taleo":
        mode = "json_api_then_bound_public_html"
        detail = "Locale-explicit public fillList bindings; locale unions and advertised counts need reconciliation."
    elif source.id == "eu_careers_static":
        mode = "public_html_and_primary_documents"
        detail = "Board summaries require official agency notices/PDFs and incorporated documents."
    elif source.id == "idb_successfactors":
        mode = "public_html_with_browser_inventory_gap"
        gaps = ["Requires an accessible rendered full board, both current terminal sort walks, and total-sized ID union; an empty RSS or unrendered widget remains incomplete."]
    elif source.id == "osce_custom_html":
        mode = "public_html_with_browser_inventory_gap"
        if source.extra.get("browser_render", {}).get("inventory") == "osce_full_search_v1":
            mode = "guarded_browser_full_search"
            gaps = []
        else:
            gaps.append("Latest-jobs feed is a subset of the full public board.")
    if source.id in {"unicef_pageup", "fao_taleo", "unv_uvp", "unops_avature", "worldbank_csod", "icddrb_custom_html", "itcilo_custom_html"}:
        gaps = []
    return {
        "source_id": source.id,
        "enabled": source.enabled,
        "transport": mode,
        "full_detail_contract": detail,
        "enumeration_contract": ("osce_full_search_v1" if source.id == "osce_custom_html" and source.extra.get("browser_render", {}).get("inventory") == "osce_full_search_v1" else None) or {"worldbank_csod": "csod_pages_v1", "icddrb_custom_html": "icddrb_custom_html_tables_v1", "itcilo_custom_html": "itcilo_custom_html_tables_v1", "unicef_pageup": "unicef_pageup_v1", "fao_taleo": "fao_taleo_locales_v1", "unv_uvp": "unv_search_pages_v1", "unops_avature": "unops_public_pages_v1"}.get(source.id) or ("idb_fullboard_dom_v1" if source.id == "idb_successfactors" and source.extra.get("public_all_jobs_url") else {"workday": "workday_cxs_v1", "oracle_hcm": "oracle_ce_v1",
                                 "smartrecruiters": "smartrecruiters_postings_v1"}.get(family, "unsupported")),
        "limitations": gaps,
        "independent_whole_public_text_verification": "not_implemented",
        "browser_fallback": "Human challenge/session or unsupported full-board controls only; shared queue/quota required.",
    }


def captured_json(metadata: dict):
    body = gzip.decompress(Path(metadata["artifact"]).read_bytes())
    if hashlib.sha256(body).hexdigest() != metadata.get("body_sha256"):
        raise ValueError("Captured enumeration response hash differs")
    return json.loads(body)


def verify_listing(source, jobs, capture_paths) -> dict:
    """Reconcile supported provider censuses independently of adapter diagnostics.

    This proves only the configured JSON endpoint/filter scope at the capture
    interval. It does not assert that a publisher exposes every vacancy there.
    """
    family = source.adapter or source.ats_family
    if source.id in {"icddrb_custom_html", "itcilo_custom_html"}:
        from jobagg.pipelines.inventory_four_source import verify_table_listing
        return verify_table_listing(source, jobs, capture_paths)
    if source.id == "worldbank_csod" or (source.id == "osce_custom_html" and source.extra.get("browser_render", {}).get("inventory") == "osce_full_search_v1"):
        from jobagg.pipelines.inventory_four_source import verify_four_source
        return verify_four_source(source, jobs, capture_paths)
    if source.id in {"unv_uvp", "unops_avature"}:
        from jobagg.pipelines.inventory_recovery_contracts import verify_recovery_listing
        return verify_recovery_listing(source, jobs, capture_paths)
    if source.id in {"unicef_pageup", "fao_taleo"}:
        from jobagg.pipelines.inventory_vacancy_contracts import verify_vacancy_listing
        return verify_vacancy_listing(source, jobs, capture_paths)
    if source.id == "idb_successfactors" and source.extra.get("public_all_jobs_url"):
        from jobagg.pipelines.inventory_idb_contract import verify_idb_listing
        return verify_idb_listing(source, jobs, capture_paths)
    if family in {"oracle_hcm", "smartrecruiters"}:
        from jobagg.pipelines.inventory_api_contracts import verify_api_listing
        return verify_api_listing(source, jobs, capture_paths, family)
    result = {
        "complete": False,
        "method": "unsupported",
        "observed_count": len(jobs),
        "scope": "configured source endpoint and filters",
        "reasons": [],
        "capture_paths": [],
    }
    if (source.adapter or source.ats_family) != "workday":
        result["reasons"] = source_capability(source)["limitations"]
        return result
    expected_url = source.extra.get("jobs_url") or (
        str(source.extra["cxs_base_url"]).rstrip("/") + "/jobs"
        if source.extra.get("cxs_base_url")
        else source.extra.get("api_url")
    )
    facets = dict(source.extra.get("applied_facets") or source.extra.get("facets") or {})
    search = str(source.extra.get("search_text") or "")
    limit = int(source.extra.get("page_size", 20))
    paths, totals, offset, count, terminal = [], set(), 0, 0, False
    try:
        for path in capture_paths:
            meta = json.loads(Path(path).read_text())
            if meta.get("phase", {}).get("kind") != "listing" or meta.get("url") != expected_url:
                continue
            if (
                meta.get("status_code") != 200
                or meta.get("response_url") != expected_url
                or meta.get("method") != "POST"
            ):
                raise ValueError("Listing endpoint did not return its exact successful response")
            request = meta.get("public_pagination_request")
            if request != {
                "appliedFacets": facets,
                "limit": limit,
                "offset": offset,
                "searchText": search,
            }:
                raise ValueError("Missing, skipped or scope-mismatched pagination request")
            # The exact bytes emitted by the maintained HTTP JSON method bind these public parameters.
            if hashlib.sha256(
                json.dumps(request, separators=(",", ":")).encode()
            ).hexdigest() != meta.get("request_body_sha256"):
                raise ValueError("Pagination parameters differ from original request bytes")
            payload = captured_json(meta)
            rows = payload.get("jobPostings") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or type(payload.get("total")) is not int:
                raise ValueError("Unrecognized Workday census structure")
            total = payload["total"]
            if total < 0:
                raise ValueError("Negative advertised total")
            # Observed CXS contract: later nonempty pages can carry omitted total=0.
            if count == 0 or total > 0 or not rows:
                totals.add(total)
            for row in rows:
                value = row.get("externalPath")
                if not isinstance(value, str) or not value.startswith("/"):
                    raise ValueError("Public posting lacks an exact external path")
                paths.append(value)
            count += 1
            offset += limit
            terminal = not rows or (len(totals) == 1 and len(paths) == next(iter(totals)))
            result["capture_paths"].append(
                {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
            )
        actual = [job.raw.get("externalPath") for job in jobs]
        if (
            not count
            or len(totals) != 1
            or not terminal
            or len(paths) != len(set(paths))
            or len(paths) != next(iter(totals))
            or len(actual) != len(set(actual))
            or set(paths) != set(actual)
        ):
            raise ValueError(
                "Duplicate/missing paths, truncated pagination or inconsistent source total"
            )
        result.update(
            complete=True,
            method="workday_cxs_v1",
            reported_total=next(iter(totals)),
            page_count=count,
            verified_zero=not paths,
        )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result["method"] = "workday_cxs_v1"
        result["reasons"].append(str(exc))
    return result
