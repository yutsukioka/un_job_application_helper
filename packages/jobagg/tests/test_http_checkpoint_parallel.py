"""Real file locks must serialize actual hosts across organization threads."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import pytest

from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.pipelines.http_checkpoint import DurableCapture, HostIneligible
from jobagg.robots import load_policy


def make_capture(root, name, transport):
    policy = root / "robots.yaml"
    if not policy.exists():
        policy.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    client = JobAggHTTPClient()
    client._request = transport
    return DurableCapture(client, load_policy(policy), root / name, {}, lock_root=root / "hosts")


def response(url):
    return HttpResponse(url, 200, {"Content-Type": "text/plain"}, "job body", b"job body")


def test_same_host_cannot_overlap_but_another_host_can(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    unexpected = []

    def slow(url, **kwargs):
        entered.set()
        assert release.wait(10)
        return response(url)

    first = make_capture(tmp_path, "first", slow)
    second = make_capture(tmp_path, "second", lambda url, **kw: unexpected.append(url))
    other = make_capture(tmp_path, "other", lambda url, **kw: response(url))
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.request, "https://shared.example/one")
        try:
            assert entered.wait(10)
            with pytest.raises(BlockingIOError):
                second.request("https://shared.example/two")
            assert other.request("https://independent.example/three").status_code == 200
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200
    assert unexpected == []


def test_access_hold_from_one_organization_applies_to_another(tmp_path):
    first = make_capture(tmp_path, "first", lambda url, **kw: response(url))
    second = make_capture(tmp_path, "second", lambda url, **kw: pytest.fail("held host fetched"))
    first.stop_host("shared.example", "Access challenge", tmp_path / "evidence")
    with pytest.raises(HostIneligible, match="Existing host stop"):
        second.request("https://shared.example/job")
    path = tmp_path / "hosts" / ("host-" + hashlib.sha256(b"shared.example").hexdigest()[:24] + ".json")
    assert json.loads(path.read_text())["stopped"] is True
