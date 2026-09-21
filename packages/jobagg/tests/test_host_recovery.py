"""The durable host circuit must recover conservatively without quota resets."""
import hashlib
import json
import socket
import ssl
import time
import urllib.error

import pytest

from jobagg.http import HTTPError, HttpResponse, JobAggHTTPClient
from jobagg.http_safe import SSRFProtectionError
from jobagg.pipelines.host_recovery import (
    begin_probe, classify_failure, host_eligibility, transient_failure, transport_success,
)
from jobagg.pipelines.http_checkpoint import DurableCapture, HostIneligible
from jobagg.robots import load_policy


def failed(state=None, now=10000, category="transient_transport"):
    return transient_failure(state or {}, now, category, "capture.json", now + 1800)


def test_second_timeout_has_bounded_cooldown_not_permanent_stop():
    first = failed()
    second = failed(first, 12000)
    assert first["eligible_at"] == 11800
    assert second["eligible_at"] == 15600
    assert not second["stopped"] and second["consecutive_transport_failures"] == 2
    assert not host_eligibility(second, 15599)["allowed"]
    assert host_eligibility(second, 15600)["allowed"]
    assert first["consecutive_transport_failures"] == 1  # pure transitions


def test_one_probe_owner_can_continue_but_competitor_cannot():
    state = begin_probe(failed(), 11800, "first")
    assert host_eligibility(state, 11801, probe_owner="first")["allowed"]
    assert not host_eligibility(state, 11801, probe_owner="second")["allowed"]
    assert not host_eligibility(state, 11801)["allowed"]
    assert state["recovery"]["probe_attempts"] == [11800]
    assert begin_probe(state, 11801, "first")["recovery"]["probe_attempts"] == [11800]


def test_abandoned_probe_charged_and_delayed_before_next_owner():
    state = begin_probe(failed(), 11800, "crashed")
    assert not host_eligibility(state, 12701)["allowed"]
    assert host_eligibility(state, 14500)["allowed"]
    recovered = begin_probe(state, 14500, "replacement")
    assert recovered["recovery"]["probe_attempts"] == [11800, 14500]
    assert recovered["recovery"]["probe_owner"] == "replacement"


def test_three_probes_per_day_and_retry_after_are_both_enforced():
    state = failed()
    for stamp in (11800, 15400, 22600):
        state = begin_probe(state, stamp, str(stamp))
        state = failed(state, stamp)
    assert state["eligible_at"] == 11800 + 86400
    assert not host_eligibility(state, state["eligible_at"] - 1)["allowed"]
    later = transient_failure(state, 22601, "rate_limit", "next.json", 999999)
    assert later["eligible_at"] == 999999


@pytest.mark.parametrize("reason", ["old timeout", "403", "TLS", "robots", "allowlist", "review"])
def test_legacy_or_review_hold_never_auto_clears(reason):
    state = {"stopped": True, "reason": reason, "eligible_at": 1}
    assert host_eligibility(state, 999999)["category"] == "review"
    assert transport_success(state, is_robots=False) == state
    assert failed(state, 999999) == state


def test_robots_success_cannot_close_recovery():
    state = begin_probe(failed(), 11800, "owner")
    assert transport_success(state, is_robots=True) == state
    healthy = transport_success(state, is_robots=False)
    assert "recovery" not in healthy and healthy["consecutive_transport_failures"] == 0
    assert healthy["last_recovery"] == state["recovery"]


@pytest.mark.parametrize("exception,expected", [
    (TimeoutError("read"), "transient_transport"),
    (ConnectionResetError("reset"), "transient_transport"),
    (socket.gaierror(socket.EAI_AGAIN, "retry DNS"), "transient_transport"),
    (ssl.SSLCertVerificationError("certificate"), "tls_validation"),
    (SSRFProtectionError("allowlist"), "local_policy"),
    (OSError(28, "disk full"), "local_failure"),
    (ValueError("parse"), "local_failure"),
    (urllib.error.HTTPError("https://host", 403, "denied", {}, None), "access_denied"),
    (urllib.error.HTTPError("https://host", 429, "rate", {}, None), "rate_limit"),
    (urllib.error.HTTPError("https://host", 503, "busy", {}, None), "transient_transport"),
    (urllib.error.HTTPError("https://host", 404, "gone", {}, None), "http_nonretryable"),
])
def test_typed_exception_classification(exception, expected):
    wrapped = HTTPError("outer")
    wrapped.__cause__ = urllib.error.URLError(exception)
    assert classify_failure(wrapped) == expected


def make_capture(tmp_path, name, transport, *, robots=False):
    policy = tmp_path / "robots.yaml"
    policy.write_text(f"default:\n  honor_robots_txt: {str(robots).lower()}\n  min_delay_seconds: 0\n")
    client = JobAggHTTPClient()
    client._request = transport
    return DurableCapture(client, load_policy(policy), tmp_path / name, {}, lock_root=tmp_path / "hosts")


def state_path(tmp_path):
    return tmp_path / "hosts" / ("host-" + hashlib.sha256(b"one.example").hexdigest()[:24] + ".json")


def good(url):
    return HttpResponse(url, 200, {}, "job body", b"job body")


def test_durable_probe_survives_captures_and_robots_does_not_unlock(tmp_path, monkeypatch):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    now = [10000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    calls = []
    def fail(url, **kw):
        calls.append(url)
        raise TimeoutError("read timeout")
    first = make_capture(tmp_path, "first", fail)
    with pytest.raises(TimeoutError):
        first.request("https://one.example/job")
    now[0] = 11800
    observed = []
    def transport(url, **kw):
        state = json.loads(state_path(tmp_path).read_text())
        observed.append(state)
        assert state["recovery"]["phase"] == "half_open"
        if url.endswith("robots.txt"):
            return HttpResponse(url, 200, {}, "User-agent: *\nAllow: /", b"User-agent: *\nAllow: /")
        other = make_capture(tmp_path, "other", lambda *a, **k: pytest.fail("competing probe dispatched"))
        with pytest.raises(BlockingIOError):
            other.request("https://one.example/other")
        return good(url)
    probe = make_capture(tmp_path, "probe", transport, robots=True)
    assert probe.request("https://one.example/job").status_code == 200
    assert len(observed) == 2 and observed[0]["recovery"]["probe_attempts"] == [11800]
    assert "recovery" not in json.loads(state_path(tmp_path).read_text())


def test_second_checkpoint_timeout_remains_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    now = [10000]
    monkeypatch.setattr(time, "time", lambda: now[0])
    def fail(url, **kw):
        raise TimeoutError("read timeout")
    first = make_capture(tmp_path, "first", fail)
    with pytest.raises(TimeoutError): first.request("https://one.example/job")
    now[0] = 11800
    second = make_capture(tmp_path, "second", fail)
    with pytest.raises(TimeoutError): second.request("https://one.example/job")
    state = json.loads(state_path(tmp_path).read_text())
    assert not state["stopped"] and state["eligible_at"] == 15400
    meta = json.loads((tmp_path / "second/http/00001.json").read_text())
    assert meta["failure_category"] == "transient_transport"


def test_capture_disk_error_is_not_remote_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    capture = make_capture(tmp_path, "capture", lambda url, **kw: good(url))
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.gzip.compress", lambda data: (_ for _ in ()).throw(OSError(28, "full")))
    with pytest.raises(OSError): capture.request("https://one.example/job")
    state = json.loads(state_path(tmp_path).read_text())
    assert "recovery" not in state and not state.get("stopped")
    meta = json.loads((tmp_path / "capture/http/00001.json").read_text())
    assert meta["failure_category"] == "local_failure"


def test_native_http_read_timeout_preserves_headers_and_partial_bytes(monkeypatch):
    client = JobAggHTTPClient(max_retries=0)
    class Headers(dict):
        def get_content_charset(self): return "utf-8"
    class Stream:
        status = 200
        headers = Headers()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        count = 0
        def read(self, size):
            self.count += 1
            if self.count == 1: return b"partial"
            raise TimeoutError("body read")
    class Opener:
        def open(self, *args, **kwargs): return Stream()
    client._opener = Opener()
    with pytest.raises(TimeoutError): client.get("https://one.example/job")
    assert client.last_request_diagnostics["stage"] == "body_read"
    assert client.last_request_diagnostics["headers_received"] is True
    assert client.last_request_diagnostics["wire_bytes_read"] == 7


def test_http_denial_keeps_its_hold_when_error_body_times_out():
    try:
        try:
            raise urllib.error.HTTPError("https://one.example/job", 403, "denied", {}, None)
        except urllib.error.HTTPError:
            raise TimeoutError("reading denial page")
    except TimeoutError as exc:
        assert classify_failure(exc) == "access_denied"


def test_corrupt_or_nontransient_recovery_state_fails_closed():
    state = failed()
    state["recovery"]["failure_kind"] = "access_denied"
    with pytest.raises(ValueError): host_eligibility(state, 999999)
    with pytest.raises(ValueError): host_eligibility({"stopped": "false"}, 999999)


def test_operator_timeout_probe_keeps_hold_for_others_and_after_expiry():
    from jobagg.pipelines.host_recovery import host_eligibility
    state={'stopped':True,'evidence':'old-timeout.json','reviewed_timeout_probe':{
        'owner':'reviewer','legacy_evidence':'old-timeout.json','failure_kind':'transient_transport','expires_at':200}}
    assert host_eligibility(state,100,probe_owner='reviewer')['allowed']
    assert not host_eligibility(state,100,probe_owner='worker')['allowed']
    assert not host_eligibility(state,201,probe_owner='reviewer')['allowed']
    assert not host_eligibility({**state,'evidence':'new-denial.json'},100,probe_owner='reviewer')['allowed']
