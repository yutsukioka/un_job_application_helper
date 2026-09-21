import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request

import pytest

from jobagg.http import HTTPError, HttpResponse, JobAggHTTPClient
from jobagg.pipelines.http_checkpoint import DurableCapture, HostIneligible, RedirectHop, safe_url
from jobagg.robots import load_policy


def capture(tmp_path, monkeypatch, transport, *, robots=False, **kwargs):
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        f"default:\n  honor_robots_txt: {str(robots).lower()}\n  min_delay_seconds: 0\n"
    )
    client = JobAggHTTPClient(
        default_headers={"Authorization": "private", "Cookie": "private", "X-Token": "private"}
    )
    client._request = transport
    result = DurableCapture(
        client,
        load_policy(policy_path),
        tmp_path / "evidence",
        {},
        lock_root=tmp_path / "hosts",
        default_header_origin="https://one.example",
        phase={"kind": "detail", "job_id": "123"},
        **kwargs,
    )
    return result


def response(url, text="Public body", status=200):
    return HttpResponse(url, status, {"Content-Type": "text/html"}, text, text.encode())


def test_durable_intent_precedes_transport_and_binary_receipt_is_exact(tmp_path, monkeypatch):
    def transport(url, **kwargs):
        intent = json.loads((tmp_path / "evidence/http/00001.json").read_text())
        assert intent["state"] == "dispatched_before_response"
        assert "finished_at" not in intent
        return response(url, "Exact public UTF8 é")

    guarded = capture(tmp_path, monkeypatch, transport)
    guarded.request("https://one.example/123", method="GET")
    meta = json.loads((tmp_path / "evidence/http/00001.json").read_text())
    body = gzip.decompress(open(meta["artifact"], "rb").read())
    assert body == "Exact public UTF8 é".encode()
    assert meta["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert meta["state"] == "response_captured" and meta["phase"]["job_id"] == "123"


def test_first_challenge_is_durable_stop_even_after_cooldown(tmp_path, monkeypatch):
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        return response(url, "<title>Just a moment</title>Verify you are human")

    guarded = capture(tmp_path, monkeypatch, transport)
    with pytest.raises(RuntimeError, match="access challenge"):
        guarded.request("https://one.example/123", method="GET")
    state_path = next((tmp_path / "hosts").glob("*.json"))
    state = json.loads(state_path.read_text())
    assert state["stopped"] is True
    state["eligible_at"] = 0
    state_path.write_text(json.dumps(state))
    with pytest.raises(HostIneligible, match="Existing host stop"):
        guarded.request("https://one.example/456", method="GET")
    assert len(calls) == 1


def test_redirect_checks_actual_destination_robots_and_strips_credentials(tmp_path, monkeypatch):
    calls = []
    guarded = None

    def transport(url, **kwargs):
        calls.append((url, dict(guarded.client.default_headers), kwargs.get("headers") or {}))
        if url.endswith("/robots.txt"):
            return response(url, "User-agent: *\nAllow: /\n")
        if url == "https://one.example/123":
            req = urllib.request.Request(
                "https://two.example/notice",
                headers={"Authorization": "bad", "Cookie": "bad", "Accept": "text/html"},
            )
            raise RedirectHop(req, 302)
        return response(url)

    guarded = capture(tmp_path, monkeypatch, transport, robots=True)
    guarded.request("https://one.example/123", method="GET")
    assert [c[0] for c in calls] == [
        "https://one.example/robots.txt",
        "https://one.example/123",
        "https://two.example/robots.txt",
        "https://two.example/notice",
    ]
    for url, defaults, headers in calls[2:]:
        assert not {key.lower() for key in (*defaults, *headers)} & {
            "authorization",
            "cookie",
            "x-token",
        }
    assert len(list((tmp_path / "hosts").glob("*.lock"))) == 2


def test_retry_after_preserved_and_transport_called_once(tmp_path, monkeypatch):
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        try:
            raise urllib.error.HTTPError(url, 429, "Slow down", {"Retry-After": "7200"}, None)
        except urllib.error.HTTPError as original:
            raise HTTPError("rate limit") from original

    guarded = capture(tmp_path, monkeypatch, transport)
    before = time.time()
    with pytest.raises(HTTPError):
        guarded.request("https://one.example/123", method="GET")
    state = json.loads(next((tmp_path / "hosts").glob("*.json")).read_text())
    assert state["eligible_at"] >= before + 7200
    assert calls == ["https://one.example/123"] and guarded.client.max_retries == 0


def test_deadline_and_redirect_downgrade_dispatch_no_extra_request(tmp_path, monkeypatch):
    calls = []
    guarded = capture(
        tmp_path,
        monkeypatch,
        lambda *args, **kwargs: calls.append(args),
        deadline_at=time.time() - 1,
    )
    with pytest.raises(HostIneligible, match="deadline"):
        guarded.request("https://one.example/123", method="GET")
    assert not calls
    guarded.deadline_at = None

    def transport(url, **kwargs):
        calls.append(url)
        raise RedirectHop(urllib.request.Request("http://one.example/123"), 302)

    guarded.original = transport
    with pytest.raises(HostIneligible, match="downgrade"):
        guarded.request("https://one.example/123", method="GET")
    assert len(calls) == 1


def test_diagnostic_url_hides_query_secret():
    assert "actual-secret" not in safe_url("https://one.example/job?token=actual-secret&id=123")
    with pytest.raises(ValueError):
        safe_url("https://user:password@one.example/job")
