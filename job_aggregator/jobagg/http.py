"""Small HTTP wrapper used by adapters."""

from __future__ import annotations

import errno
import json
import random
import socket
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class HTTPError(RuntimeError):
    pass


class JobAggHTTPClient:
    def __init__(
        self,
        *,
        user_agent: str = "jobagg/0.1",
        timeout_seconds: int = 30,
        min_delay_seconds: float = 0.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        jitter_ratio: float = 0.25,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.jitter_ratio = jitter_ratio
        self._last_request_at = 0.0
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        request_headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        for attempt in range(self.max_retries + 1):
            self._respect_min_delay()
            try:
                with self._opener.open(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode(response.headers.get_content_charset() or "utf-8")
                    self._last_request_at = time.monotonic()
                    return HttpResponse(
                        url=response.geturl(),
                        status_code=response.status,
                        headers=dict(response.headers.items()),
                        text=body,
                    )
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                response_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                    delay = retry_after or self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(self._with_jitter(delay))
                    continue
                raise HTTPError(
                    f"{method} {url} failed with HTTP {exc.code}: {response_body[:300]}"
                ) from exc
            except urllib.error.URLError as exc:
                self._last_request_at = time.monotonic()
                if _is_transient_url_error(exc) and attempt < self.max_retries:
                    delay = self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(self._with_jitter(delay))
                    continue
                raise HTTPError(f"{method} {url} failed: {exc.reason}") from exc

        raise HTTPError(f"{method} {url} failed after retries")

    def _respect_min_delay(self) -> None:
        if self.min_delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _with_jitter(self, delay: float) -> float:
        if delay <= 0 or self.jitter_ratio <= 0:
            return max(delay, 0)
        low = max(0, delay * (1 - self.jitter_ratio))
        high = delay * (1 + self.jitter_ratio)
        return random.uniform(low, high)

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpResponse:
        return self._request(url, method="GET", headers=headers)

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._request(url, method="POST", headers=request_headers, body=body)

    def post_form(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        request_headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request_headers.update(headers or {})
        body = None
        if payload:
            from urllib.parse import urlencode

            body = urlencode(payload, doseq=True).encode("utf-8")
        return self._request(url, method="POST", headers=request_headers, body=body)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


_TRANSIENT_ERRNOS = {
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ETIMEDOUT,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
}
_TRANSIENT_REASON_MARKERS = (
    "connection reset",
    "connection aborted",
    "remote end closed",
    "temporarily unavailable",
    "temporary failure",
    "timed out",
)


def _is_transient_url_error(exc: urllib.error.URLError) -> bool:
    reason = exc.reason
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    if isinstance(reason, OSError) and reason.errno in _TRANSIENT_ERRNOS:
        return True
    text = str(reason).lower()
    return any(marker in text for marker in _TRANSIENT_REASON_MARKERS)
