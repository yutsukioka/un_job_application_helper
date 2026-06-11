"""Small HTTP wrapper used by adapters."""

from __future__ import annotations

import errno
import gzip
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.cookiejar import CookieJar
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - optional dependency
    import brotli  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised only when brotli is missing
    brotli = None


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


class ResponseTooLargeError(HTTPError):
    """Raised when a response exceeds the configured byte cap."""


# 50 MiB. Listing JSON and HTML pages from supported ATSs are typically
# under 5 MiB; this cap exists to prevent a misconfigured detail URL from
# pulling a large binary into memory and persisting it as ``description``.
_DEFAULT_MAX_RESPONSE_BYTES = 50 * 1024 * 1024


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
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.jitter_ratio = jitter_ratio
        self.max_response_bytes = int(max_response_bytes)
        # Per-host last-request timestamp. Robots policies promise "one
        # request per host every ``min_delay_seconds``" — a single shared
        # timestamp would over-throttle when the same client straddles
        # multiple hosts (e.g. listing API + CDN attachment fetch).
        self._last_request_at_by_host: dict[str, float] = {}
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: int | float | None = None,
    ) -> HttpResponse:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": _default_accept_encoding(),
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        for attempt in range(self.max_retries + 1):
            self._respect_min_delay(host)
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            if int(declared) > self.max_response_bytes:
                                raise ResponseTooLargeError(
                                    f"Response from {url} declares {declared} bytes, exceeds cap {self.max_response_bytes}"
                                )
                        except ValueError:
                            pass
                    # Read at most max_response_bytes + 1 so we can detect
                    # over-cap responses that omitted Content-Length.
                    raw_bytes = response.read(self.max_response_bytes + 1)
                    if len(raw_bytes) > self.max_response_bytes:
                        raise ResponseTooLargeError(
                            f"Response from {url} exceeded cap of {self.max_response_bytes} bytes"
                        )
                    decoded_bytes = _decode_content_encoding(
                        raw_bytes,
                        response.headers.get("Content-Encoding"),
                    )
                    charset = response.headers.get_content_charset() or "utf-8"
                    text = decoded_bytes.decode(charset, errors="replace")
                    self._mark_request(host)
                    return HttpResponse(
                        url=response.geturl(),
                        status_code=response.status,
                        headers=dict(response.headers.items()),
                        text=text,
                    )
            except urllib.error.HTTPError as exc:
                self._mark_request(host)
                error_bytes = _decode_content_encoding(
                    exc.read(),
                    exc.headers.get("Content-Encoding") if exc.headers else None,
                )
                response_body = error_bytes.decode("utf-8", errors="replace")
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
                self._mark_request(host)
                if _is_transient_url_error(exc) and attempt < self.max_retries:
                    delay = self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(self._with_jitter(delay))
                    continue
                raise HTTPError(f"{method} {url} failed: {exc.reason}") from exc

        raise HTTPError(f"{method} {url} failed after retries")

    def _respect_min_delay(self, host: str) -> None:
        if self.min_delay_seconds <= 0:
            return
        last = self._last_request_at_by_host.get(host)
        if last is None:
            return
        elapsed = time.monotonic() - last
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _mark_request(self, host: str) -> None:
        self._last_request_at_by_host[host] = time.monotonic()

    def _with_jitter(self, delay: float) -> float:
        if delay <= 0 or self.jitter_ratio <= 0:
            return max(delay, 0)
        low = max(0, delay * (1 - self.jitter_ratio))
        high = delay * (1 + self.jitter_ratio)
        return random.uniform(low, high)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> HttpResponse:
        return self._request(
            url,
            method="GET",
            headers=headers,
            timeout_seconds=timeout_seconds,
        )

    def post_json(
        self,
        url: str,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
    ) -> HttpResponse:
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._request(
            url,
            method="POST",
            headers=request_headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )

    def post_form(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | float | None = None,
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
        return self._request(
            url,
            method="POST",
            headers=request_headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )


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


def _default_accept_encoding() -> str:
    encodings = ["gzip", "deflate"]
    if brotli is not None:
        encodings.append("br")
    return ", ".join(encodings)


def _decode_content_encoding(data: bytes, encoding: str | None) -> bytes:
    """Decode an HTTP response body according to its Content-Encoding header.

    Returns the original bytes when the encoding is missing, ``identity``, or
    cannot be decoded. ``urllib`` does not transparently decode response
    bodies, so adapters used to receive raw gzip bytes whenever a CDN
    compressed the payload regardless of ``Accept-Encoding``.
    """

    if not data or not encoding:
        return data
    encoding = encoding.strip().lower()
    if encoding in {"", "identity"}:
        return data
    try:
        if encoding == "gzip":
            return gzip.decompress(data)
        if encoding == "deflate":
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
        if encoding == "br" and brotli is not None:
            return brotli.decompress(data)
    except (OSError, zlib.error, ValueError):
        return data
    return data
