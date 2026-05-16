import io
import urllib.error

import pytest

from jobagg.http import HTTPError, JobAggHTTPClient


class FakeHeaders(dict):
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    status = 200
    headers = FakeHeaders({"Content-Type": "text/plain"})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b"ok"

    def geturl(self):
        return "https://example.org"


class RetryThenSuccessOpener:
    def __init__(self):
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        if self.calls < 3:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return FakeResponse()


class ForbiddenOpener:
    def __init__(self):
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(b"blocked"),
        )


class ServerErrorThenSuccessOpener:
    def __init__(self):
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                502,
                "Bad Gateway",
                {},
                io.BytesIO(b"gateway timeout"),
            )
        return FakeResponse()


def test_transient_url_error_retries_then_succeeds():
    client = JobAggHTTPClient(max_retries=2, backoff_base_seconds=0, jitter_ratio=0)
    opener = RetryThenSuccessOpener()
    client._opener = opener

    response = client.get("https://example.org")

    assert response.text == "ok"
    assert opener.calls == 3


def test_transient_http_5xx_retries_then_succeeds():
    client = JobAggHTTPClient(max_retries=1, backoff_base_seconds=0, jitter_ratio=0)
    opener = ServerErrorThenSuccessOpener()
    client._opener = opener

    response = client.get("https://example.org")

    assert response.text == "ok"
    assert opener.calls == 2


def test_retry_attempts_respect_min_delay(monkeypatch):
    now = [100.0]
    sleeps = []

    def fake_monotonic():
        return now[0]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("jobagg.http.time.monotonic", fake_monotonic)
    monkeypatch.setattr("jobagg.http.time.sleep", fake_sleep)
    client = JobAggHTTPClient(
        max_retries=1,
        backoff_base_seconds=0,
        jitter_ratio=0,
        min_delay_seconds=2,
    )
    opener = ServerErrorThenSuccessOpener()
    client._opener = opener

    response = client.get("https://example.org")

    assert response.text == "ok"
    assert opener.calls == 2
    assert sleeps == [2]


def test_http_403_is_not_retried():
    client = JobAggHTTPClient(max_retries=3, backoff_base_seconds=0, jitter_ratio=0)
    opener = ForbiddenOpener()
    client._opener = opener

    with pytest.raises(HTTPError):
        client.get("https://example.org/forbidden")

    assert opener.calls == 1
