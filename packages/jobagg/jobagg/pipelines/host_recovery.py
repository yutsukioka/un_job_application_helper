"""Typed host failures and bounded durable transport recovery.

Legacy or explicit ``stopped`` holds are deliberately never migrated here.
The checkpoint owns the host flock while applying these pure state functions.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import errno
import math
import socket
import ssl
import urllib.error

from jobagg.http_safe import SSRFProtectionError


@dataclass(frozen=True)
class RecoveryPolicy:
    initial_cooldown_seconds: int = 1800
    maximum_cooldown_seconds: int = 21600
    probe_lease_seconds: int = 900
    max_probes_per_window: int = 3
    probe_window_seconds: int = 86400


DEFAULT_RECOVERY_POLICY = RecoveryPolicy()


def epoch(value):
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Recovery timestamp lacks timezone")
        value = parsed.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Malformed recovery timestamp")
    return float(value)


def recovery_state(state):
    recovery = state.get("recovery")
    if recovery is None:
        return None
    if not isinstance(recovery, dict) or recovery.get("schema_version") != 1:
        raise ValueError("Malformed host recovery state")
    if recovery.get("phase") not in {"cooldown", "half_open"}:
        raise ValueError("Unknown host recovery phase")
    if recovery.get("failure_kind") not in {"transient_transport", "rate_limit"}:
        raise ValueError("Non-transient host recovery state")
    failures = recovery.get("failures")
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 1:
        raise ValueError("Malformed host recovery failure count")
    epoch(recovery.get("eligible_at"))
    probes = recovery.get("probe_attempts", [])
    if not isinstance(probes, list) or len(probes) > 3:
        raise ValueError("Malformed host recovery probe history")
    for stamp in probes:
        epoch(stamp)
    if probes != sorted(probes):
        raise ValueError("Out-of-order host recovery probe history")
    if recovery["phase"] == "half_open":
        if not isinstance(recovery.get("probe_owner"), str) or not recovery["probe_owner"]:
            raise ValueError("Missing recovery probe owner")
        epoch(recovery.get("probe_until"))
    return recovery


def host_eligibility(state, now, *, probe_owner=None, policy=DEFAULT_RECOVERY_POLICY):
    """Read-only admission, with an additional cooldown after an abandoned probe."""
    if not isinstance(state.get("stopped", False), bool):
        raise ValueError("Malformed host review hold")
    if state.get("stopped"):
        csrf = state.get("reviewed_osce_csrf_probe") or {}
        if (probe_owner and csrf.get("owner") == probe_owner
                and csrf.get("evidence") == state.get("evidence")
                and state.get("failure_category") == "access_denied"
                and csrf.get("source_id") == "osce_custom_html"
                and csrf.get("csrf") == "osce_tss_token_v1"
                and now < epoch(csrf.get("expires_at", 0))):
            return {"allowed": True, "category": "ready", "eligible_at": now}
        data = state.get("reviewed_osce_data_probe") or {}
        if (probe_owner and data.get("owner") == probe_owner
                and data.get("evidence") == state.get("evidence")
                and state.get("failure_category") == "access_denied"
                and data.get("source_id") == "osce_custom_html"
                and data.get("data_route") == "osce_job_results_v1"
                and now < epoch(data.get("expires_at", 0))):
            return {"allowed": True, "category": "ready", "eligible_at": now}
        native = state.get("reviewed_native_browser_probe") or {}
        if (probe_owner and native.get("owner") == probe_owner
                and native.get("evidence") == state.get("evidence")
                and state.get("failure_category") == "access_denied"
                and native.get("source_id") == "osce_custom_html"
                and native.get("transport") == "chromium_cdp_native_v1"
                and now < epoch(native.get("expires_at", 0))):
            return {"allowed": True, "category": "ready", "eligible_at": now}
        repair = state.get("reviewed_configuration_probe") or {}
        if (probe_owner and repair.get("owner") == probe_owner
                and repair.get("evidence") == state.get("evidence")
                and state.get("failure_category") == "access_denied"
                and repair.get("repair") in {"csod_anonymous_context", "osce_omit_stylesheets"}
                and now < epoch(repair.get("expires_at", 0))):
            return {"allowed": True, "category": "ready", "eligible_at": now}
        # An operator may authorize one bounded owner to review a legacy timeout.
        # The hold remains in place for every other worker and on interruption.
        probe = state.get("reviewed_timeout_probe") or {}
        if (probe_owner and probe.get("owner") == probe_owner
                and probe.get("legacy_evidence") == state.get("evidence")
                and probe.get("failure_kind") == "transient_transport"
                and now < epoch(probe.get("expires_at", 0))):
            return {"allowed": True, "category": "ready", "eligible_at": now}
        return {"allowed": False, "category": "review", "eligible_at": 0.0}
    recovery = recovery_state(state)
    # Legacy eligible_at values are normalized by checkpoint/worker callers.
    due = epoch(state.get("eligible_at", 0))
    if recovery:
        due = max(due, recovery["eligible_at"])
        if recovery["phase"] == "half_open":
            if recovery["probe_owner"] == probe_owner and now < recovery["probe_until"]:
                return {"allowed": True, "category": "ready", "eligible_at": now}
            due = max(due, recovery["probe_until"] + policy.initial_cooldown_seconds)
        recent = [stamp for stamp in recovery.get("probe_attempts", []) if stamp > now - policy.probe_window_seconds]
        if len(recent) >= policy.max_probes_per_window:
            due = max(due, recent[0] + policy.probe_window_seconds)
    return {"allowed": due <= now, "category": "ready" if due <= now else "cooldown", "eligible_at": due}


def authorize_osce_csrf_probe(state, evidence, source, *, owner, now, expires_at, access_review):
    """One reviewed repair for a bound pre-CSRF pagination POST denial."""
    import hashlib
    import json
    from urllib.parse import parse_qsl, urlsplit
    from jobagg.osce_fragments import CSRF_CONTRACT, DATA_ROUTE, validate_request

    contract = source.extra.get("browser_render", {})
    diagnostics = evidence.get("transport_diagnostics", {})
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    if (source.id != "osce_custom_html" or state.get("stopped") is not True
            or state.get("failure_category") != "access_denied" or not state.get("evidence")
            or state.get("inherited_stop") or state.get("recovery")
            or evidence.get("status_code") != 403 or evidence.get("method") != "POST"
            or evidence.get("error_type") != "HTTPError" or evidence.get("failure_category") != "access_denied"
            or evidence.get("phase", {}).get("kind") != "listing"
            or evidence.get("transport") != "chromium_cdp_native_v1"
            or diagnostics.get("request_body_bytes") != 0 or "csrf_observation" in diagnostics
            or contract.get("transport") != "chromium_cdp_native_v1"
            or contract.get("data_route") != DATA_ROUTE or contract.get("csrf") != CSRF_CONTRACT
            or contract.get("inventory") != "osce_full_search_v1"
            or source.extra.get("listing_url") != "https://vacancies.osce.org/jobs/search/"
            or not owner or not 0 < epoch(expires_at) - epoch(now) <= 600
            or access_review.get("observation") != "reviewed_missing_csrf_header"
            or access_review.get("evidence_sha256") != digest
            or access_review.get("provider_csrf_rule_verified") is not True
            or access_review.get("current_document_token_present") is not True
            or not 0 <= now - epoch(access_review.get("observed_at", 0)) <= 86400):
        raise ValueError("Hold/review does not qualify for OSCE CSRF recovery")
    query = dict(parse_qsl(urlsplit(evidence.get("url", "")).query))
    validate_request(evidence["url"], "POST", None, session=query.get("JobSearch.id", ""),
                     site=query.get("site-name", ""), page=2)
    if (state.get("osce_csrf_attempt") or {}).get("evidence") == state["evidence"]:
        raise ValueError("OSCE CSRF probe already attempted for this denial")
    value = deepcopy(state)
    value["reviewed_osce_csrf_probe"] = {
        "owner": owner, "evidence": state["evidence"], "source_id": source.id,
        "csrf": CSRF_CONTRACT, "authorized_at": now, "expires_at": expires_at,
        "evidence_sha256": digest,
    }
    value["osce_csrf_attempt"] = {"evidence": state["evidence"], "authorized_at": now}
    return value


def authorize_osce_data_probe(state, evidence, source, *, owner, now, expires_at, access_review):
    """One HAR-grounded data-route repair for the native js-dict denial only."""
    import re
    from urllib.parse import urlsplit
    contract = source.extra.get("browser_render", {})
    url = urlsplit(evidence.get("url", ""))
    if (source.id != "osce_custom_html" or state.get("stopped") is not True
            or state.get("failure_category") != "access_denied" or not state.get("evidence")
            or state.get("inherited_stop") or state.get("recovery")
            or url.scheme != "https" or url.netloc != "vacancies.osce.org" or url.path != "/js-dict"
            or evidence.get("status_code") != 403 or evidence.get("method") != "GET"
            or evidence.get("error_type") != "HTTPError" or evidence.get("failure_category") != "access_denied"
            or evidence.get("phase", {}).get("kind") != "listing"
            or evidence.get("transport") != "chromium_cdp_native_v1"
            or contract.get("transport") != "chromium_cdp_native_v1"
            or contract.get("data_route") != "osce_job_results_v1"
            or contract.get("inventory") != "osce_full_search_v1"
            or source.extra.get("listing_url") != "https://vacancies.osce.org/jobs/search/"
            or not owner or not 0 < epoch(expires_at) - epoch(now) <= 600
            or access_review.get("observation") != "har_verified_full_listing"
            or access_review.get("complete_captured_listing") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", access_review.get("har_sha256", ""))
            or not 0 <= now - epoch(access_review.get("observed_at", 0)) <= 86400):
        raise ValueError("Hold/review does not qualify for OSCE fragment recovery")
    if (state.get("osce_data_attempt") or {}).get("evidence") == state["evidence"]:
        raise ValueError("OSCE data-route probe already attempted for this denial")
    value = deepcopy(state)
    value["reviewed_osce_data_probe"] = {
        "owner": owner, "evidence": state["evidence"], "source_id": source.id,
        "data_route": "osce_job_results_v1", "authorized_at": now, "expires_at": expires_at,
        "har_sha256": access_review["har_sha256"],
    }
    value["osce_data_attempt"] = {"evidence": state["evidence"], "authorized_at": now}
    return value


def authorize_native_browser_probe(state, evidence, source, *, owner, now, expires_at, access_review):
    """One explicitly reviewed transport change; never re-probe a native denial."""
    contract = source.extra.get("browser_render", {})
    url = "https://vacancies.osce.org/jobs/search/"
    if (source.id != "osce_custom_html" or state.get("stopped") is not True
            or state.get("failure_category") != "access_denied" or not state.get("evidence")
            or state.get("inherited_stop") or state.get("recovery")
            or evidence.get("url") != url or evidence.get("status_code") != 403
            or evidence.get("method") != "GET" or evidence.get("error_type") != "HTTPError"
            or evidence.get("failure_category") != "access_denied"
            or evidence.get("phase", {}).get("kind") != "listing"
            or evidence.get("transport") == "chromium_cdp_native_v1"
            or contract.get("transport") != "chromium_cdp_native_v1"
            or contract.get("inventory") != "osce_full_search_v1"
            or contract.get("load_stylesheets") is not False
            or source.extra.get("listing_url", source.base_url) != url
            or not owner or not 0 < epoch(expires_at) - epoch(now) <= 600
            or access_review.get("url") != url
            or access_review.get("observation") != "user_confirmed_browser_access"
            or not 0 <= now - epoch(access_review.get("observed_at", 0)) <= 86400):
        raise ValueError("Hold/review does not qualify for OSCE native browser recovery")
    previous = state.get("native_browser_attempt")
    if previous and previous.get("evidence") == state["evidence"]:
        raise ValueError("Native browser probe already attempted for this denial")
    value = deepcopy(state)
    value["reviewed_native_browser_probe"] = {
        "owner": owner, "evidence": state["evidence"], "source_id": source.id,
        "transport": "chromium_cdp_native_v1", "authorized_at": now, "expires_at": expires_at,
    }
    value["native_browser_attempt"] = {"evidence": state["evidence"], "authorized_at": now}
    return value


def authorize_configuration_probe(state, evidence, source, *, owner, now, expires_at):
    """Explicit operator repair, never called by ordinary retry scheduling.

    Caller must read state/evidence under the shared worker and host locks. Only
    the two evidenced configuration mistakes qualify; all other denials remain
    stopped. A new failure changes evidence and invalidates this owner's lease.
    """
    from urllib.parse import urlsplit

    if (state.get("stopped") is not True
            or state.get("failure_category") != "access_denied"
            or not state.get("evidence") or state.get("inherited_stop") or state.get("recovery")
            or evidence.get("failure_category") != "access_denied"
            or evidence.get("error_type") != "HTTPError"
            or evidence.get("phase", {}).get("kind") != "listing"
            or not owner or not 0 < epoch(expires_at) - epoch(now) <= 600):
        raise ValueError("Hold is not eligible for a bounded configuration repair probe")
    extra = source.extra
    url = urlsplit(evidence.get("url", ""))
    repair = None
    if (source.id == "worldbank_csod"
            and evidence.get("url") == extra.get("api_url")
            and evidence.get("url") == "https://us.api.csod.com/rec-job-search/external/jobs"
            and evidence.get("status_code") == 401 and evidence.get("method") == "POST"
            and "no Authorization header found" in evidence.get("error", "")
            and extra.get("requires_bearer_token") is True
            and extra.get("requires_runtime_context") is True
            and urlsplit(extra.get("context_url", "")).hostname == "worldbankgroup.csod.com"):
        repair = "csod_anonymous_context"
    elif (source.id == "osce_custom_html" and url.scheme == "https"
            and url.hostname == "vacancies.osce.org" and url.path == "/styles/core.css"
            and evidence.get("status_code") == 403 and evidence.get("method") == "GET"
            and extra.get("browser_render", {}).get("inventory") == "osce_full_search_v1"
            and extra.get("browser_render", {}).get("load_stylesheets") is False):
        repair = "osce_omit_stylesheets"
    if repair is None:
        raise ValueError("Denial evidence does not match a repaired source configuration")
    value = deepcopy(state)
    value["reviewed_configuration_probe"] = {
        "owner": owner, "evidence": state["evidence"], "repair": repair,
        "source_id": source.id, "authorized_at": now, "expires_at": expires_at,
    }
    return value


def begin_probe(state, now, owner, *, policy=DEFAULT_RECOVERY_POLICY):
    """Reserve before dispatch; an interrupted reservation is never refunded."""
    value = deepcopy(state)
    recovery = recovery_state(value)
    if not recovery:
        return value
    admission = host_eligibility(value, now, probe_owner=owner, policy=policy)
    if not admission["allowed"]:
        raise ValueError("Recovery probe is not eligible")
    if recovery["phase"] == "half_open" and recovery["probe_owner"] == owner and now < recovery["probe_until"]:
        return value
    probes = [stamp for stamp in recovery.get("probe_attempts", []) if stamp > now - policy.probe_window_seconds]
    probes.append(now)
    recovery.update(phase="half_open", probe_owner=owner, probe_until=now + policy.probe_lease_seconds, probe_attempts=probes)
    return value


def transient_failure(state, now, category, evidence, retry_after_floor, *, policy=DEFAULT_RECOVERY_POLICY):
    value = deepcopy(state)
    if value.get("stopped"):
        return value
    previous = recovery_state(value)
    failures = (previous["failures"] if previous else 0) + 1
    delay = min(policy.maximum_cooldown_seconds, policy.initial_cooldown_seconds * 2 ** min(failures - 1, 10))
    probes = [stamp for stamp in (previous or {}).get("probe_attempts", []) if stamp > now - policy.probe_window_seconds]
    due = max(now + delay, retry_after_floor)
    if len(probes) >= policy.max_probes_per_window:
        due = max(due, probes[0] + policy.probe_window_seconds)
    value.update(stopped=False, eligible_at=due, failure_category=category, evidence=str(evidence),
                 consecutive_transport_failures=failures,
                 recovery={"schema_version": 1, "phase": "cooldown", "failure_kind": category,
                           "failures": failures, "eligible_at": due, "probe_attempts": probes})
    return value


def transport_success(state, *, is_robots):
    value = deepcopy(state)
    if value.get("stopped") or is_robots:
        return value
    previous = value.pop("recovery", None)
    if previous:
        value["last_recovery"] = previous
        value.update(reason="Transport recovered after bounded probe", failure_category=None)
    value.update(consecutive_transport_failures=0, eligible_at=0)
    return value


def exception_chain(exc):
    pending, seen = [exc], set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        yield error
        if isinstance(error, urllib.error.URLError) and isinstance(error.reason, Exception):
            pending.append(error.reason)
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        elif error.__context__ is not None and not error.__suppress_context__:
            pending.append(error.__context__)


def classify_failure(exc):
    """Only call for exceptions from transport, not local capture persistence."""
    chain = list(exception_chain(exc))
    if any(isinstance(error, SSRFProtectionError) for error in chain):
        return "local_policy"
    if any(isinstance(error, ssl.SSLError) for error in chain):
        return "tls_validation"
    statuses = [error.code for error in chain if isinstance(error, urllib.error.HTTPError)]
    if statuses:
        status = statuses[0]
        if status in {401, 403}:
            return "access_denied"
        if status == 429:
            return "rate_limit"
        return "transient_transport" if status in {408, 500, 502, 503, 504} else "http_nonretryable"
    transient_errnos = {errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}
    if any(isinstance(error, (TimeoutError, ConnectionError)) or
           (isinstance(error, OSError) and error.errno in transient_errnos) or
           (isinstance(error, socket.gaierror) and error.errno == socket.EAI_AGAIN) for error in chain):
        return "transient_transport"
    return "local_failure"


def release_reviewed_allowlist_hold(state, evidence, *, host, reviewed_hosts, reviewed_at):
    """Explicit maintenance migration, never an automatic network-policy bypass.

    The caller validates the revised source policy and holds the shared owner lock.
    Only legacy allowlist failures with matching saved capture evidence qualify.
    Existing pacing, counters and unrelated holds are preserved.
    """
    from urllib.parse import urlsplit

    if (host not in reviewed_hosts
            or state.get("stopped") is not True
            or state.get("reason") != "HTTP transport failure: SSRFProtectionError"
            or state.get("inherited_stop") or state.get("recovery")
            or state.get("failure_category") not in (None, "local_policy")
            or evidence.get("error_type") != "SSRFProtectionError"
            or evidence.get("error") != "SSRFProtectionError: URL host is not in the organization allowlist"
            or urlsplit(evidence.get("url", "")).hostname != host
            or evidence.get("status_code") is not None):
        raise ValueError("Legacy hold is not an evidenced reviewed allowlist rejection")
    return {**state, "stopped": False, "failure_category": "local_policy_repaired",
            "reason": "Reviewed attachment allowlist configuration repaired",
            "allowlist_repair": {"reviewed_at": reviewed_at, "host": host,
                                 "prior_reason": state["reason"], "evidence": state.get("evidence")}}
