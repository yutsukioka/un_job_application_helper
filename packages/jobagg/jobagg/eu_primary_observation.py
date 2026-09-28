"""Portable binding for reviewed EU primary-PDF text, without certifying metadata."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, urlsplit

MARKER = "_eu_primary_text_spacing_resolution"
FIELDS = ("title", "location", "department", "employment_type", "posted_at", "closes_at",
          "closes_at_local", "closes_tz")


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _url(value):
    if not isinstance(value, str):
        return None
    url = urlsplit(value)
    if url.scheme != "https" or not url.netloc or url.netloc != url.hostname or url.fragment:
        return None
    return url


def _url_key(value):
    url = _url(value)
    return (url.netloc, url.path, parse_qsl(url.query, keep_blank_values=True)) if url else None


def _instant(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _bound(raw, row):
    proof = raw.get(MARKER)
    source, identity = row["source_id"], str(row["external_id"])
    key = source + ":" + identity
    if (source != "eu_careers_static" or not isinstance(proof, dict)
            or proof.get("record_kind") != "detail" or proof.get("source_id") != source
            or proof.get("external_id") != identity or proof.get("job_key") != key
            or proof.get("metadata_completeness_certified") is not False
            or proof.get("whole_job_complete") is not False
            or proof.get("ordered_nonwhitespace_characters_unchanged") is not True
            or proof.get("all_ordered_pages_match_body") is not True
            or raw.get("parser") != "eu_official_detail" or raw.get("external_id") != identity
            or raw.get("identity_verification") != "official_link_and_title_or_reference"):
        return False
    summary, official = _url(row["source_url"]), _url(row["apply_url"])
    if (not summary or summary.netloc != "eu-careers.europa.eu" or summary.query
            or not re.fullmatch(r"/en/job-opportunities/[^/]+/" + re.escape(identity), summary.path)
            or not official or proof.get("source_url") != row["source_url"]
            or proof.get("apply_url") != row["apply_url"] or raw.get("detail_url") != row["source_url"]
            or raw.get("official_vacancy_url") != row["apply_url"]):
        return False
    hosts = (("eu-lisa-", "erecruitment.eulisa.europa.eu"), ("euosha-", "euosha.gestmax.eu"),
             ("etf-", "www.etf.europa.eu"), ("euaa-", "careers.euaa.europa.eu"),
             ("f4e-", "f4e-jobs.gestmax.eu"))
    if not any(identity.startswith(prefix) and official.netloc == host for prefix, host in hosts):
        return False
    is_euaa = identity.startswith("euaa-")
    if (is_euaa and (official.path != "/" or official.query)) or (not is_euaa and not official.path.lower().endswith(".pdf")):
        return False
    retained = proof.get("retained_normalized_fields")
    if not isinstance(retained, dict) or set(retained) != set(FIELDS):
        return False
    for field in FIELDS:
        if field in {"posted_at", "closes_at"}:
            if _instant(retained[field]) != _instant(row[field]):
                return False
        elif retained[field] != row[field]:
            return False
    notice, description = raw.get("official_notice_text"), row["description"]
    if (not isinstance(notice, str) or not notice.strip() or not isinstance(description, str)
            or notice not in description or _sha(description) != proof.get("description_after_sha256")
            or _sha(notice) != proof.get("official_notice_text_sha256")):
        return False
    for name in ("description_before_sha256", "original_official_notice_text_sha256"):
        if not _hex(proof.get(name)):
            return False
    document = proof.get("primary_document_snapshot")
    if not isinstance(document, dict):
        return False
    if (document.get("job_key") != key or document.get("source_id") != source
            or _url_key(document.get("url")) != _url_key(row["apply_url"])
            or document.get("final_url") != document.get("url")
            or not _hex(document.get("content_sha256"))):
        return False
    text, units, count = document.get("extracted_text"), document.get("units"), document.get("page_count")
    if (not isinstance(text, str) or _sha(text) != document.get("text_sha256")
            or type(count) is not int or count <= 0 or not isinstance(units, list) or len(units) != count
            or any(not isinstance(unit, dict) or unit.get("page") != page
                   or not isinstance(unit.get("text"), str) for page, unit in enumerate(units, 1))):
        return False
    # The extraction artifact labels each original page. Those labels are
    # transport structure; compare the ordered page text with the notice.
    labelled = "\n\n".join(f"[Page {unit['page']}]\n{unit['text']}" for unit in units)
    if text != labelled or "".join("".join(unit["text"].split()) for unit in units) != "".join(notice.split()):
        return False
    retrieval = document.get("retrieval")
    if (not isinstance(retrieval, dict) or retrieval.get("status_code") != 200
            or retrieval.get("method") != ("POST" if is_euaa else "GET")
            or retrieval.get("url") != document["url"] or retrieval.get("response_url") != document["url"]
            or retrieval.get("body_sha256") != document["content_sha256"]
            or retrieval.get("phase") != {"kind": "detail", "job_id": identity}
            or retrieval.get("started_at") != proof.get("original_capture_started_at")
            or retrieval.get("finished_at") != proof.get("original_capture_finished_at")):
        return False
    started, finished = _instant(retrieval["started_at"]), _instant(retrieval["finished_at"])
    if not isinstance(started, datetime) or not isinstance(finished, datetime) or not started.tzinfo or not finished.tzinfo or started > finished:
        return False
    ref = proof.get("current_reviewed_document")
    if not isinstance(ref, dict):
        return False
    for path_key, hash_key in (("path", "sha256"), ("retrieval_metadata", "retrieval_metadata_sha256")):
        path = ref.get(path_key)
        if (not isinstance(path, str) or not PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts or not _hex(ref.get(hash_key))):
            return False
    visual = proof.get("visual_scope")
    if not isinstance(visual, dict) or visual.get("job_key") != key or visual.get("content_sha256") != document["content_sha256"]:
        return False
    viewed, rendered = visual.get("pages_actually_viewed"), visual.get("rendered_page_sha256")
    if (not isinstance(viewed, list) or not viewed or len(set(viewed)) != len(viewed)
            or any(type(page) is not int or page < 1 or page > count for page in viewed)
            or not isinstance(rendered, dict) or set(rendered) != {str(page) for page in viewed}
            or not all(_hex(value) for value in rendered.values())):
        return False
    reviewed_retrieval = visual.get("current_retrieval_validation")
    if (not isinstance(reviewed_retrieval, dict)
            or reviewed_retrieval.get("content_sha256") != document["content_sha256"]
            or reviewed_retrieval.get("request_metadata") != ref["retrieval_metadata"]):
        return False
    return True


def bound_public_text(raw, row):
    """Validate a stored observation locally; capture files were checked at import."""
    try:
        return _bound(raw, row)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False
