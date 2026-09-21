"""Narrow regression exception for captured Taleo date-header changes.

This pure check does not fetch, publish, or certify a full public text contract.
The publisher must first validate the current detail artifact and its HTTP bytes.
Only known visible DOM date bindings may differ; all remaining text must match.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

_METHOD = "taleo_bound_date_headers_v1"


def inspira_ltr_mark_only_change(before, incoming):
    """Allow only an LRM change in otherwise identical left-to-right text.

    Keep the original strings/hashes. Do not strip arbitrary Unicode format
    controls: joiners and bidi overrides can change meaning or presentation.
    The caller must already have validated the fresh detail capture/identity.
    """
    old, new = before.get("description") or "", incoming.get("description") or ""
    if (before.get("source_id") != "un_inspira" or incoming.get("source_id") != "un_inspira"
            or not incoming.get("external_id") or before.get("external_id") != incoming.get("external_id")
            or not old or not new or old == new
            or any(unicodedata.bidirectional(ch) in {"R", "AL", "RLE", "RLO", "RLI", "LRE", "LRO", "LRI", "FSI", "PDI", "PDF"} for ch in old + new)
            or old.replace("\u200e", "") != new.replace("\u200e", "")):
        return {"accepted": False}
    return {"accepted": True, "method": "inspira_ltr_mark_only_v1",
            "removed_codepoint": "U+200E", "source_bytes_preserved": True}
_SOURCES = {
    "fao_taleo": ("jobs.fao.org", "Job Posting", "Closure Date", "dmy_slash"),
    "wipo_taleo": ("wipo.taleo.net", "Publication Date", "Application Deadline", "dmy"),
    "who_taleo": ("careers.who.int", "Job Posting", "Closing Date", "mdy"),
    "iaea_taleo": ("iaea.taleo.net", "Job Posting", "Closing Date", "ymd"),
    "adb_taleo": (
        "adb.taleo.net",
        "Job Posting",
        "Closing Date (Period for Applying) - Internal",
        "dmy",
    ),
}
_MONTHS = {
    month: index
    for index, month in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
_DATE_PATTERNS = {
    "dmy_slash": r"(?P<day>\d{1,2})/(?P<month>[A-Z][a-z]{2})/(?P<year>\d{4})",
    "dmy": r"(?P<day>\d{1,2})-(?P<month>[A-Z][a-z]{2})-(?P<year>\d{4})",
    "mdy": r"(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4})",
    "ymd": r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})",
}


def _raw(row):
    value = row.get("raw") if "raw" in row else json.loads(row.get("raw_json") or "{}")
    if not isinstance(value, dict):
        raise ValueError("raw_public_fields_missing")
    return value


def _normal(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def _parse_display(value, style):
    pattern = (
        _DATE_PATTERNS[style]
        + r"(?:, (?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2}) (?P<meridian>AM|PM))?"
    )
    match = re.fullmatch(pattern, value)
    if not match:
        raise ValueError("unknown_public_date_format")
    parts = match.groupdict()
    month = int(parts["month"]) if style == "ymd" else _MONTHS[parts["month"]]
    hour = int(parts["hour"] or 0)
    has_time = parts["hour"] is not None
    if has_time:
        if not 1 <= hour <= 12:
            raise ValueError("invalid_public_clock")
        hour = hour % 12 + (12 if parts["meridian"] == "PM" else 0)
    return datetime(
        int(parts["year"]),
        month,
        int(parts["day"]),
        hour,
        int(parts["minute"] or 0),
        int(parts["second"] or 0),
    ), has_time


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("normalized_datetime_missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("normalized_datetime_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _typed_date(row, raw, entry, field, source_contract):
    hostname, _, _, style = source_contract
    value = entry["public_text"]
    resolution_key = (
        "_taleo_posting_time_resolution" if field == "posted_at" else "_taleo_deadline_resolution"
    )
    resolution = raw.get(resolution_key)
    if not isinstance(resolution, dict) or resolution.get("public_value") != value:
        raise ValueError("public_date_resolution_not_bound")
    if field == "closes_at" and resolution != raw.get("_taleo_deadline_timezone_evidence"):
        raise ValueError("closing_timezone_proof_differs")
    if raw.get("_taleo_flat", {}).get(entry["public_label"]) != value:
        raise ValueError("flat_public_date_differs_from_visible_binding")
    url = resolution.get("url")
    if not isinstance(url, str):
        raise ValueError("date_resolution_url_not_bound")
    def same_url(actual):
        if actual == url:
            return True
        # Historical FAO date proofs predate explicit English locale URLs.
        # Only adding lang=en is equivalent; identity, timezone and all other
        # query parameters must remain exactly bound to the recorded URL.
        if hostname != "jobs.fao.org" or not isinstance(actual, str):
            return False
        old, new = urlsplit(url), urlsplit(actual)
        old_query, new_query = parse_qs(old.query), parse_qs(new.query)
        if "lang" in old_query or new_query.pop("lang", None) != ["en"]:
            return False
        return old._replace(query="") == new._replace(query="") and old_query == new_query
    if not same_url(row.get("source_url")) or not same_url(row.get("apply_url")):
        raise ValueError("date_resolution_url_not_bound")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if parts.scheme != "https" or parts.hostname != hostname or parts.username or parts.password:
        raise ValueError("public_date_url_host_differs")
    if not parts.path.endswith("/jobdetail.ftl") or query.get("job") != [str(row["external_id"])]:
        raise ValueError("public_date_url_identity_differs")
    tzname = resolution.get("tzname")
    if query.get("tzname") != [tzname] or len(query.get("tz", [])) != 1:
        raise ValueError("public_date_url_timezone_differs")
    zone = ZoneInfo(tzname)
    naive, has_time = _parse_display(value, style)
    local = naive.replace(tzinfo=zone)
    if local.utcoffset() != naive.replace(tzinfo=zone, fold=1).utcoffset():
        raise ValueError("ambiguous_public_date_clock")
    if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError("nonexistent_public_date_clock")
    offset = int(local.utcoffset().total_seconds())
    offset_label = (
        f"GMT{'+' if offset >= 0 else '-'}{abs(offset) // 3600:02d}:{abs(offset) % 3600 // 60:02d}"
    )
    if query["tz"] != [offset_label]:
        raise ValueError("public_date_url_offset_differs")
    if resolution.get("kind") == "known_instant":
        if not has_time or _instant(row.get(field)) != local.astimezone(timezone.utc):
            raise ValueError("normalized_datetime_differs_from_public_date")
        normalized = local.astimezone(timezone.utc).isoformat()
    elif field == "posted_at" and resolution.get("kind") == "public_calendar_date_only":
        if has_time or row.get(field) is not None:
            raise ValueError("calendar_only_publication_date_has_invented_instant")
        normalized = naive.date().isoformat()
    else:
        raise ValueError("unsupported_public_date_resolution")
    if field == "closes_at" and (
        row.get("closes_at_local") != value or row.get("closes_tz") != tzname
    ):
        raise ValueError("closing_public_scalar_fields_differ")
    return {
        "field": field,
        "public_label": entry["public_label"],
        "public_value": value,
        "tzname": tzname,
        "url": url,
        "normalized_value": normalized,
        "kind": resolution["kind"],
    }


def _body_without_dates(row, contract):
    raw = _raw(row)
    bindings = raw.get("_taleo_flat", {}).get("_taleo_public_bindings", {})
    if bindings.get("kind") != "paired_public_dom_bindings":
        raise ValueError("paired_visible_dom_bindings_missing")
    entries = bindings.get("visible_fields")
    if not isinstance(entries, list):
        raise ValueError("visible_public_fields_missing")
    text = row.get("description")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("public_description_missing")
    dates = []
    targets = {"reqPostingDate", "reqUnpostingDate"}
    for target, semantic, field, label in (
        ("reqPostingDate", "reqlistitem.postingdate", "posted_at", contract[1]),
        ("reqUnpostingDate", "reqlistitem.unpostingdate", "closes_at", contract[2]),
    ):
        matches = [entry for entry in entries if entry.get("target") == target]
        if len(matches) != 1:
            raise ValueError("missing_or_duplicate_public_date_binding")
        entry = matches[0]
        if entry.get("semantic") != semantic or entry.get("public_label") != label:
            raise ValueError("unknown_public_date_header")
        dates.append(_typed_date(row, raw, entry, field, contract))
        # Only a standalone, exact labelled public header may be replaced. A
        # number/date appearing inside responsibilities is never removed.
        pattern = re.compile(
            r"(?m)^"
            + re.escape(label)
            + r"[ \t]*\r?\n[ \t]*"
            + re.escape(entry["public_text"])
            + r"[ \t]*(?=\r?$)"
        )
        text, count = pattern.subn(label + "\n<BOUND_" + field.upper() + ">", text)
        if count != 1:
            raise ValueError("public_date_header_not_unique_in_description")
    unchanged_bindings = [
        {key: entry.get(key) for key in ("target", "semantic", "public_label", "public_text")}
        for entry in entries
        if entry.get("target") not in targets
    ]
    return _normal(text), dates, unchanged_bindings


def taleo_date_metadata_only_change(before, incoming):
    """Return auditable acceptance only for known, typed Taleo date headers.

    Actual date changes (including deadline extensions) are allowed when both
    old/new dates bind their respective public DOM values, timezone URL, and
    normalized scalar fields. This never exempts another prose/metadata change.
    """
    result = {"accepted": False, "method": _METHOD, "completeness_certified": False}
    try:
        if not before or incoming.get("source_id") not in _SOURCES:
            raise ValueError("unsupported_source_or_missing_baseline")
        if before.get("source_id") != incoming.get("source_id") or str(
            before.get("external_id")
        ) != str(incoming.get("external_id")):
            raise ValueError("source_or_native_identity_differs")
        if before.get("ats_family") != "taleo" or incoming.get("ats_family") != "taleo":
            raise ValueError("non_taleo_public_body")
        if any(
            before.get(field) != incoming.get(field)
            for field in ("title", "location", "department", "employment_type")
        ):
            raise ValueError("non_date_public_scalar_field_differs")
        contract = _SOURCES[incoming["source_id"]]
        old_body, old_dates, old_bindings = _body_without_dates(before, contract)
        new_body, new_dates, new_bindings = _body_without_dates(incoming, contract)
        if old_body != new_body or old_bindings != new_bindings:
            raise ValueError("non_date_public_text_or_binding_differs")
        changes = [
            {"field": old["field"], "before": old, "incoming": new}
            for old, new in zip(old_dates, new_dates)
            if old != new
        ]
        if not changes:
            raise ValueError("no_bound_date_metadata_change")
        result.update(
            accepted=True,
            reason="Only validated public date headers differ; all other public text/bindings agree",
            non_date_text_sha256=hashlib.sha256(old_body.encode()).hexdigest(),
            date_changes=changes,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        result["reason"] = str(exc)
    return result
