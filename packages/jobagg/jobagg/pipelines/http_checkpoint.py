"""Durable, bounded HTTP capture for deterministic remediation workers.

Every actual redirect host is checked and paced. HTTP failures may create a
bounded cooldown or an explicit review hold; semantic parser errors belong to
the job task, not to an unrelated host. Callers hold the shared writer owner.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import email.utils
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urljoin

from jobagg.http import HttpResponse
from jobagg.robots import BlankLineSafeRobotFileParser
from jobagg.atomic_files import atomic_write_text
from jobagg.pipelines.host_recovery import (
    begin_probe, classify_failure, exception_chain, host_eligibility,
    transient_failure, transport_success,
)


def safe_url(url):
    """Do not persist credentials or query secrets in transport diagnostics."""
    from urllib.parse import unquote, urlunsplit

    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError("Credential-bearing URL rejected")
    sensitive = {
        "token",
        "access_token",
        "auth",
        "authorization",
        "cookie",
        "password",
        "secret",
        "api_key",
        "sig",
        "signature",
        "x-amz-signature",
    }
    query = "&".join(
        part.split("=", 1)[0] + "=%5Bredacted%5D"
        if unquote(part.split("=", 1)[0]).lower() in sensitive
        else part
        for part in parts.query.split("&")
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def utc():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    def redact(match):
        try:
            return safe_url(match[0])
        except ValueError:
            return "[credential-bearing URL]"

    return type(exc).__name__ + ": " + re.sub(r"https?://[^\s<>\"']+", redact, str(exc))[:700]


def save(path, value):
    atomic_write_text(path, json.dumps(value, indent=2, default=str) + "\n")


class HostIneligible(RuntimeError):
    def __init__(self, message, *, category="review", eligible_at=None):
        super().__init__(message)
        self.category = category
        self.eligible_at = eligible_at


class RedirectHop(Exception):
    def __init__(self, request, status):
        self.request = request
        self.status = status


class OneHopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        # Unwind urllib before the next network request so the old host lock
        # is released and the destination's shared floor can be enforced.
        raise RedirectHop(redirected, code)


class CapturedRobotsChecker:
    """One cached, paced robots request per origin, through the same transport."""

    def __init__(self, capture):
        self.capture = capture
        self.parsers = {}

    def allowed(self, url):
        parts = urlsplit(url)
        if not self.capture.policy.honor_robots_for(parts.netloc):
            return True
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self.parsers:
            robots_url = urljoin(root, "/robots.txt")
            response = self.capture.request(robots_url, method="GET", _is_robots=True)
            parser = BlankLineSafeRobotFileParser()
            parser.set_url(robots_url)
            if response.status_code in (400, 404, 410):
                parser.allow_all = True
            else:
                parser.parse(response.text.splitlines())
            self.parsers[root] = parser
        return self.parsers[root].can_fetch(self.capture.policy.user_agent, url)


def finite_epoch(value):
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Shared host eligibility lacks timezone")
        value = parsed.timestamp()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Malformed shared host eligibility clock")
    return float(value)


def retry_floor(retry_after, now):
    floor = now + 1800
    if retry_after:
        try:
            seconds = float(retry_after)
            if math.isfinite(seconds) and seconds >= 0:
                floor = max(floor, now + seconds)
        except ValueError:
            try:
                floor = max(floor, email.utils.parsedate_to_datetime(retry_after).timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
    return floor


class DurableCapture:
    def __init__(
        self,
        client,
        policy,
        target,
        denied_hosts,
        *,
        lock_root,
        max_requests=160,
        forbidden_paths=(),
        default_header_origin=None,
        phase=None,
        deadline_at=None,
    ):
        self.client, self.policy, self.target = client, policy, Path(target)
        self.lock_root = Path(lock_root)
        self.forbidden_paths = tuple(forbidden_paths)
        self.phase = phase
        self.deadline_at = deadline_at
        self.denied_hosts = dict(denied_hosts)
        self.checker = CapturedRobotsChecker(self)
        self.original = client._request
        self.count, self.dispatched, self.max_requests = 0, 0, max_requests
        self.current_id, self.last_host = None, None
        self.probe_owner = hashlib.sha256(str(self.target.resolve()).encode()).hexdigest()
        self.default_header_origin = (
            self.origin(default_header_origin) if default_header_origin else None
        )
        self.client.max_retries = 0
        self.lock_root.mkdir(parents=True, exist_ok=True)
        (self.target / "http").mkdir(parents=True, exist_ok=True)
        if not self.client.tls_verify:
            raise ValueError("Recovery requires verified TLS")
        self.client._opener = self.client._build_opener(OneHopRedirect())

    @staticmethod
    def origin(url):
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    def stop_host(self, host, reason, evidence):
        """Persist caller's first parser failure without making a request."""
        self.denied_hosts[host] = reason
        lock_path = self.lock_root / (
            "host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".lock"
        )
        with lock_path.open("a+") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state_path = lock_path.with_suffix(".json")
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            state.update(stopped=True, reason=reason, evidence=str(evidence), stopped_at=utc())
            save(state_path, state)

    def transport(self, url, **kwargs):
        # _request rebuilds headers, so stripping only the redirect Request
        # would accidentally re-add default Cookie/Authorization values.
        defaults = self.client.default_headers
        public = {"accept", "accept-encoding", "accept-language", "user-agent", "cache-control"}
        if self.origin(url) != self.default_header_origin:
            self.client.default_headers = {k: v for k, v in defaults.items() if k.lower() in public}
        try:
            return self.original(url, **kwargs)
        finally:
            self.client.default_headers = defaults

    @contextmanager
    def host_lock(self, host):
        lock_path = self.lock_root / (
            "host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".lock"
        )
        with lock_path.open("a+") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state_path = lock_path.with_suffix(".json")
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            eligibility = host_eligibility(state, time.time(), probe_owner=self.probe_owner)
            if host in self.denied_hosts or eligibility["category"] == "review":
                raise HostIneligible("Existing host stop: " + host)
            if not eligibility["allowed"]:
                raise HostIneligible(
                    "Existing host cooldown: " + host,
                    category="cooldown",
                    eligible_at=eligibility["eligible_at"],
                )
            owner.seek(0)
            previous = float(owner.read().strip() or "0")
            if not math.isfinite(previous) or previous < 0:
                raise ValueError("Malformed host pacing timestamp")
            pause = max(8.0, self.policy.min_delay_for(host), self.client.min_delay_seconds) - (
                time.time() - previous
            )
            if pause > 0:
                self._check_deadline(pause)
                time.sleep(pause)
            state = begin_probe(state, time.time(), self.probe_owner)
            # Fsync the half-open claim before any request; a crash keeps the
            # probe charged and enforces its lease plus abandonment cooldown.
            if state.get("recovery"):
                save(state_path, state)
            try:
                yield state_path, state
            finally:
                completed = time.time()
                owner.seek(0)
                owner.truncate()
                owner.write(str(completed))
                owner.flush()
                os.fsync(owner.fileno())
                state["last_request_at"] = completed
                save(state_path, state)

    def _check_deadline(self, delay=0):
        if self.deadline_at is not None and time.time() + delay >= self.deadline_at:
            raise HostIneligible("Bounded worker deadline reached", category="budget")

    def complete_redirect_probe(self, host):
        """A final non-robots response also completes its redirecting probe host."""
        lock_path = self.lock_root / ("host-" + hashlib.sha256(host.encode()).hexdigest()[:24] + ".lock")
        with lock_path.open("a+") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = lock_path.with_suffix(".json")
            state = json.loads(path.read_text()) if path.exists() else {}
            recovery = state.get("recovery") or {}
            if not state.get("stopped") and recovery.get("probe_owner") == self.probe_owner:
                save(path, transport_success(state, is_robots=False))

    def request(self, url, *, _depth=0, _is_robots=False, _native_dispatch=None, **kwargs):
        self._check_deadline()
        configured_timeout = float(kwargs.get("timeout_seconds") or self.client.timeout_seconds)
        if self.deadline_at is not None:
            kwargs["timeout_seconds"] = max(
                0.1,
                min(
                    float(kwargs.get("timeout_seconds") or self.client.timeout_seconds),
                    self.deadline_at - time.time(),
                ),
            )
        host = (urlsplit(url).hostname or "").lower()
        if self.default_header_origin is None:
            self.default_header_origin = self.origin(url)
        self.last_host = host
        if any(part in urlsplit(url).path for part in self.forbidden_paths):
            raise ValueError("Recovery must not refetch the listing")
        if _depth > 10:
            raise ValueError("Bounded redirect ceiling reached")
        if self.count >= self.max_requests:
            raise HostIneligible("Bounded request ceiling reached", category="budget")
        if host in self.denied_hosts:
            raise HostIneligible("Existing host stop: " + host)
        if not _is_robots and not self.checker.allowed(url):
            raise HostIneligible("Existing robots policy blocks " + safe_url(url))
        if self.count >= self.max_requests:
            raise HostIneligible("Bounded request ceiling reached after robots check", category="budget")
        self.count += 1
        record_path = self.target / "http" / f"{self.count:05d}.json"
        phase = (
            dict(self.phase)
            if self.phase is not None
            else {"kind": "detail", "job_id": self.current_id}
        )
        if _is_robots:
            phase.update(originating_phase=phase.get("kind"), kind="robots")
        record = {
            "number": self.count,
            "external_id": self.current_id,
            "phase": phase,
            "url": safe_url(url),
            "request_url_sha256": hashlib.sha256(url.encode()).hexdigest(),
            "method": kwargs.get("method"),
            "started_at": utc(),
            "configured_timeout_seconds": configured_timeout,
        }
        if _native_dispatch is not None:
            record["transport"] = "chromium_cdp_native_v1"
        request_body = kwargs.get("body")
        if isinstance(request_body, bytes):
            record["request_body_sha256"] = hashlib.sha256(request_body).hexdigest()
            try:
                public = json.loads(request_body)
                if isinstance(public, dict) and set(public) == {
                    "appliedFacets",
                    "limit",
                    "offset",
                    "searchText",
                }:
                    record["public_pagination_request"] = public
                elif urlsplit(url).path == "/rec-job-search/external/jobs" and isinstance(public, dict):
                    from jobagg.pipelines.inventory_four_source import CSOD_FIELDS
                    if set(public).issubset(CSOD_FIELDS) and {"pageNumber", "pageSize"}.issubset(public):
                        record["public_pagination_request"] = public
            except (ValueError, UnicodeError):
                pass
        redirect = None
        try:
            with self.host_lock(host) as (state_path, state):
                stage = "local_pre_dispatch"
                try:
                    self.last_host = host
                    self._check_deadline()
                    if self.deadline_at is not None:
                        kwargs["timeout_seconds"] = min(
                            float(kwargs["timeout_seconds"]), self.deadline_at - time.time()
                        )
                    self.dispatched += 1
                    record["state"] = "dispatched_before_response"
                    record["effective_timeout_seconds"] = float(kwargs.get("timeout_seconds") or self.client.timeout_seconds)
                    record["worker_budget_remaining_seconds"] = (
                        max(0, self.deadline_at - time.time()) if self.deadline_at is not None else None
                    )
                    save(record_path, record)
                    stage = "transport"
                    if _native_dispatch is not None:
                        observer = getattr(self, "native_observer", None)
                        response = (observer(url, _native_dispatch=_native_dispatch, **kwargs)
                                    if observer else _native_dispatch(url, **kwargs))
                    else:
                        response = self.transport(url, **kwargs)
                    stage = "capture_persistence"
                    diagnostics = getattr(self.client, "last_request_diagnostics", None)
                    if diagnostics:
                        record["transport_diagnostics"] = dict(diagnostics)
                    body = response.content or response.text.encode()
                    artifact = record_path.with_suffix(".body.gz")
                    with artifact.open("xb") as body_file:
                        body_file.write(gzip.compress(body))
                        body_file.flush()
                        os.fsync(body_file.fileno())
                    record.update(
                        status_code=response.status_code,
                        response_url=safe_url(response.url),
                        response_url_sha256=hashlib.sha256(response.url.encode()).hexdigest(),
                        artifact=str(artifact),
                        body_captured=True,
                        body_bytes=len(body),
                        body_sha256=hashlib.sha256(body).hexdigest(),
                        response_headers={
                            k: v
                            for k, v in response.headers.items()
                            if k.lower() in {"content-type", "last-modified", "etag", "retry-after"}
                        },
                    )
                    # Native Chromium pauses before releasing the response to
                    # the page. Persist errors too, then apply the same holds.
                    if response.status_code >= 400:
                        raise urllib.error.HTTPError(url, response.status_code,
                                                     "Captured HTTP denial/error", response.headers, None)
                    if _native_dispatch is not None and 300 <= response.status_code < 400:
                        # CDP intercepts the next hop independently, before
                        # dispatch. A redirect is not a successful probe.
                        record["state"] = "response_captured"
                        return response
                    challenge = re.search(
                        r"checking your browser|cf-chl-|verify you are human|<title[^>]*>\s*(?:just a moment|access denied|access forbidden)",
                        response.text[:15000],
                        re.I,
                    )
                    if challenge or (response.status_code == 202 and not body.strip()):
                        state.update(
                            stopped=True,
                            reason="Fresh access challenge or empty202",
                            evidence=str(record_path),
                            failure_category="access_denied",
                        )
                        record["failure_category"] = "access_denied"
                        raise RuntimeError("Fresh host access challenge: " + host)
                    if state.get("recovery") and not _is_robots:
                        state["last_recovery_success"] = {"observed_at": utc(), "evidence": str(record_path)}
                    state.update(transport_success(state, is_robots=_is_robots))
                    if not _is_robots:
                        state.pop("recovery", None)
                    record["state"] = "response_captured"
                    return response
                except RedirectHop as exc:
                    redirect = exc.request
                    record.update(
                        status_code=exc.status,
                        redirect_url=safe_url(redirect.full_url),
                        body_captured=False,
                        state="redirect_captured",
                    )
                except Exception as exc:
                    record.setdefault("body_captured", False)
                    cause = next((error for error in exception_chain(exc) if isinstance(error, urllib.error.HTTPError)), None)
                    status = cause.code if cause is not None else None
                    retry = (
                        cause.headers.get("Retry-After")
                        if isinstance(cause, urllib.error.HTTPError) and cause.headers
                        else None
                    )
                    if status is not None:
                        record["status_code"] = status
                    if retry:
                        record["retry_after"] = retry
                    if _is_robots and status in (400, 404, 410):
                        record.update(
                            robots_policy_result="robots_not_present_allow", body_captured=False
                        )
                        return HttpResponse(url, status, dict(cause.headers or {}), "", b"")
                    category = record.get("failure_category") or (
                        classify_failure(exc) if stage == "transport" or status is not None else "local_failure"
                    )
                    record.update(failure_category=category, failure_stage=stage)
                    diagnostics = getattr(self.client, "last_request_diagnostics", None)
                    if stage == "transport" and diagnostics:
                        record["transport_diagnostics"] = dict(diagnostics)
                        if diagnostics.get("headers_received") and diagnostics.get("status_code") is not None:
                            record.setdefault("status_code", diagnostics["status_code"])
                    if category in {"transient_transport", "rate_limit"}:
                        state.update(transient_failure(state, time.time(), category, record_path, retry_floor(retry, time.time())))
                        state["reason"] = "HTTP transport failure: " + type(exc).__name__
                    elif category in {"access_denied", "tls_validation"}:
                        state.update(stopped=True, failure_category=category,
                                     reason="HTTP review hold: " + type(exc).__name__, evidence=str(record_path))
                    raise
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=safe_error(exc))
            raise
        finally:
            record["finished_at"] = utc()
            record["state"] = (
                "failed" if "error_type" in record else record.get("state", "redirect_captured")
            )
            save(record_path, record)
        if redirect is not None:
            target = redirect.full_url
            if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                raise HostIneligible("HTTPS downgrade redirect rejected")
            if self.client.safe_policy:
                target = self.client.safe_policy.validate_redirect(
                    url, target, redirect_count=_depth + 1
                )
            headers = dict(redirect.header_items())
            if (urlsplit(url).scheme, host, urlsplit(url).port) != (
                urlsplit(target).scheme,
                urlsplit(target).hostname,
                urlsplit(target).port,
            ):
                public = {
                    "accept",
                    "accept-encoding",
                    "accept-language",
                    "user-agent",
                    "cache-control",
                }
                headers = {k: v for k, v in headers.items() if k.lower() in public}
            response = self.request(
                target,
                method=redirect.get_method(),
                headers=headers,
                body=redirect.data,
                timeout_seconds=kwargs.get("timeout_seconds"),
                _depth=_depth + 1,
                _is_robots=_is_robots,
            )
            if not _is_robots:
                self.complete_redirect_probe(host)
            return response
        raise RuntimeError("Missing response/redirect outcome")
