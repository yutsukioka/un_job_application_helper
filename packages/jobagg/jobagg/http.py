"""Small HTTP wrapper used by adapters."""

from __future__ import annotations

import errno
import email.utils
import gzip
import http.client
import ipaddress
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.cookiejar import CookieJar
from dataclasses import dataclass
from typing import Any

from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError, ValidatedEndpoint


def verified_ssl_context() -> ssl.SSLContext:
    """Use platform trust roots when available, without changing global SSL state.

    A missing optional import uses Python's verified default trust store. An
    installed truststore that cannot initialize fails closed; it must not cause
    certificate or hostname checks to be disabled.
    """
    try:
        import truststore
    except ImportError:
        context = ssl.create_default_context()
    else:
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError("HTTPS requires certificate and hostname verification")
    return context


@dataclass(slots=True)
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    text: str
    content: bytes = b""

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
_RESPONSE_READ_CHUNK_BYTES = 64 * 1024


def _connect_validated(endpoint: ValidatedEndpoint, timeout, source_address=None):
    """Connect numeric sockets only; Host and TLS SNI remain on HTTPConnection."""
    last_error = None
    for address in endpoint.addresses:
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        destination = (address, endpoint.port, 0, 0) if family == socket.AF_INET6 else (address, endpoint.port)
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(destination)
            return sock
        except OSError as exc:
            last_error = exc
            if sock is not None:
                sock.close()
    if last_error is not None:
        raise last_error
    raise SSRFProtectionError("No validated connection addresses")


def _connection_factory(client, request, connection_class):
    if client.safe_policy is None:
        return connection_class
    if request.has_proxy() or request._tunnel_host:
        raise SSRFProtectionError("Proxies cannot be used with pinned HTTP destinations")
    endpoint = getattr(request, "_jobagg_endpoint", None)
    if endpoint is None:
        endpoint = client.safe_policy.resolve_url(request.full_url)

    def create(host, **kwargs):
        connection = connection_class(host, **kwargs)
        if connection.host.casefold().strip("[]") != endpoint.host or connection.port != endpoint.port:
            raise SSRFProtectionError("Connection authority differs from validated URL")
        # Override only this connection's TCP dialer. HTTPSConnection still wraps
        # the socket with the original hostname and verified SSL context.
        def dial(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
            if address != (connection.host, endpoint.port):
                raise SSRFProtectionError("Connection destination changed after validation")
            return _connect_validated(endpoint, timeout, source_address)
        connection._create_connection = dial
        return connection
    return create


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, client):
        super().__init__()
        self.client = client

    def http_open(self, request):
        return self.do_open(_connection_factory(self.client, request, http.client.HTTPConnection), request)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, client):
        super().__init__(context=client._ssl_context)
        self.client = client

    def https_open(self, request):
        return self.do_open(
            _connection_factory(self.client, request, http.client.HTTPSConnection),
            request, context=self._context,
        )


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Check every destination before urllib follows it or forwards credentials."""

    def __init__(self, client: JobAggHTTPClient) -> None:
        self.client = client

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        count = getattr(req, '_jobagg_redirect_count', 0) + 1
        endpoint = None
        if self.client.safe_policy is not None:
            if count > self.client.safe_policy.max_redirects:
                raise SSRFProtectionError("Too many redirects")
            newurl = urllib.parse.urljoin(req.full_url, newurl)
            endpoint = self.client.safe_policy.resolve_url(newurl)
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if old.scheme == 'https' and new.scheme != 'https':
            raise SSRFProtectionError('HTTPS redirect downgrade is not allowed')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        redirected._jobagg_redirect_count = count
        redirected._jobagg_endpoint = endpoint
        old_origin = (old.scheme, old.hostname, old.port or (443 if old.scheme == 'https' else 80))
        new_origin = (new.scheme, new.hostname, new.port or (443 if new.scheme == 'https' else 80))
        if old_origin != new_origin:
            # Cookies from the jar are added separately for the destination's
            # own scope. Never forward an explicit Cookie/auth/custom token.
            public_headers = {'accept', 'accept-encoding', 'accept-language', 'user-agent', 'cache-control'}
            for name, _ in list(redirected.header_items()):
                if name.lower() not in public_headers:
                    redirected.remove_header(name)
        self.client._mark_request((old.hostname or '').lower())
        self.client._respect_min_delay((new.hostname or '').lower())
        self.client._mark_request((new.hostname or '').lower())
        return redirected


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
        tls_verify: bool = True,
        default_headers: dict[str, str] | None = None,
        safe_policy: SafeHTTPPolicy | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.jitter_ratio = jitter_ratio
        self.max_response_bytes = int(max_response_bytes)
        self.tls_verify = bool(tls_verify)
        self.default_headers = dict(default_headers or {})
        self.safe_policy = safe_policy
        # Per-host last-request timestamp. Robots policies promise "one
        # request per host every ``min_delay_seconds``" — a single shared
        # timestamp would over-throttle when the same client straddles
        # multiple hosts (e.g. listing API + CDN attachment fetch).
        self._last_request_at_by_host: dict[str, float] = {}
        self._cookie_jar = CookieJar()
        if self.tls_verify:
            self._ssl_context = verified_ssl_context()
        else:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self._ssl_context = context
        self._opener = self._build_opener()

    def _build_opener(self, redirect_handler=None):
        """Keep the same TLS context and cookie jar when redirect policy changes."""
        if self.tls_verify and (
            self._ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not self._ssl_context.check_hostname
        ):
            raise ValueError("HTTPS requires certificate and hostname verification")
        # A proxy would perform its own destination resolution, bypassing pinning.
        proxy = urllib.request.ProxyHandler({}) if self.safe_policy is not None else urllib.request.ProxyHandler()
        return urllib.request.build_opener(
            proxy,
            urllib.request.HTTPCookieProcessor(self._cookie_jar),
            redirect_handler if redirect_handler is not None else _ValidatedRedirectHandler(self),
            _PinnedHTTPHandler(self),
            _PinnedHTTPSHandler(self),
        )

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout_seconds: int | float | None = None,
    ) -> HttpResponse:
        diagnostic_start = time.monotonic()
        self.last_request_diagnostics = {
            "stage": "policy_validation", "headers_received": False,
            "wire_bytes_read": 0, "elapsed_seconds": 0.0,
        }
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": _default_accept_encoding(),
        }
        request_headers.update(self.default_headers)
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        for attempt in range(self.max_retries + 1):
            if self.safe_policy is not None:
                request._jobagg_endpoint = self.safe_policy.resolve_url(url)
            self._respect_min_delay(host)
            try:
                self.last_request_diagnostics["stage"] = "connect_or_headers"
                with self._opener.open(request, timeout=timeout) as response:
                    self.last_request_diagnostics.update(
                        stage="body_read", headers_received=True, status_code=response.status,
                    )
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            if int(declared) > self.max_response_bytes:
                                raise ResponseTooLargeError(
                                    f"Response from {url} declares {declared} bytes, exceeds cap {self.max_response_bytes}"
                                )
                        except ValueError:
                            pass
                    raw_bytes = _read_capped_body(
                        response,
                        url=url,
                        max_bytes=self.max_response_bytes,
                        progress=lambda total: self.last_request_diagnostics.update(wire_bytes_read=total),
                    )
                    self.last_request_diagnostics["stage"] = "body_decode"
                    decoded_bytes = _decode_content_encoding(
                        raw_bytes,
                        response.headers.get("Content-Encoding"),
                        max_bytes=self.max_response_bytes,
                    )
                    charset = response.headers.get_content_charset() or "utf-8"
                    text = decoded_bytes.decode(charset, errors="replace")
                    self._mark_request(host)
                    self.last_request_diagnostics["stage"] = "complete"
                    return HttpResponse(
                        url=response.geturl(),
                        status_code=response.status,
                        headers=dict(response.headers.items()),
                        text=text,
                        content=decoded_bytes,
                    )
            except urllib.error.HTTPError as exc:
                self._mark_request(host)
                self.last_request_diagnostics.update(stage="error_body_read", headers_received=True, status_code=exc.code)
                error_bytes = _decode_content_encoding(
                    _read_capped_body(exc, url=url, max_bytes=self.max_response_bytes,
                                     progress=lambda total: self.last_request_diagnostics.update(wire_bytes_read=total)),
                    exc.headers.get("Content-Encoding") if exc.headers else None,
                    max_bytes=self.max_response_bytes,
                )
                response_body = error_bytes.decode("utf-8", errors="replace")
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = _retry_after_from_headers(exc.headers)
                    delay = retry_after if retry_after is not None else self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(max(delay, self._with_jitter(delay)) if retry_after is not None else self._with_jitter(delay))
                    continue
                raise HTTPError(
                    f"{method} {url} failed with HTTP {exc.code}: {response_body[:300]}"
                ) from exc
            except urllib.error.URLError as exc:
                self._mark_request(host)
                if isinstance(exc.reason, SSRFProtectionError):
                    raise HTTPError(f"{method} {url} blocked by safe HTTP policy: {exc.reason}") from exc
                if _is_transient_url_error(exc) and attempt < self.max_retries:
                    delay = self.backoff_base_seconds * (2**attempt)
                    if delay > 0:
                        time.sleep(self._with_jitter(delay))
                    continue
                raise HTTPError(f"{method} {url} failed: {exc.reason}") from exc
            finally:
                self.last_request_diagnostics["elapsed_seconds"] = max(0.0, time.monotonic() - diagnostic_start)

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


def _retry_after_from_headers(headers: Any) -> float | None:
    if not headers:
        return None
    for name in ("Retry-After", "X-Retry-After", "X-Oracle-Retry-After"):
        delay = _retry_after_seconds(headers.get(name))
        if delay is not None:
            return delay
    for name in ("Retry-After-Ms", "X-Retry-After-Ms", "X-Oracle-Retry-After-Ms"):
        delay_ms = _retry_after_seconds(headers.get(name))
        if delay_ms is not None:
            return delay_ms / 1000
    for name in ("X-RateLimit-Reset", "X-Rate-Limit-Reset", "X-Oracle-RateLimit-Reset"):
        reset_at = _retry_after_seconds(headers.get(name))
        if reset_at is not None:
            return max(0.0, reset_at - time.time())
    return None


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        return max(0.0, parsed.timestamp() - time.time())


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
    return "gzip, deflate"


def _read_capped_body(stream: Any, *, url: str, max_bytes: int, progress=None) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(_RESPONSE_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if progress is not None:
            progress(total)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"Response from {url} exceeded cap of {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_content_encoding(data: bytes, encoding: str | None, *, max_bytes: int | None = None) -> bytes:
    """Decode an HTTP response body according to its Content-Encoding header.

    Returns the original bytes when the encoding is missing, ``identity``, or
    cannot be decoded. ``urllib`` does not transparently decode response
    bodies, so adapters used to receive raw gzip bytes whenever a CDN
    compressed the payload regardless of ``Accept-Encoding``.
    """

    if not data or not encoding:
        return _ensure_decoded_size(data, max_bytes)
    encoding = encoding.strip().lower()
    if encoding in {"", "identity"}:
        return _ensure_decoded_size(data, max_bytes)
    try:
        if encoding == "gzip":
            return _decode_zlib_content(data, 16 + zlib.MAX_WBITS, max_bytes=max_bytes)
        if encoding == "deflate":
            try:
                return _decode_zlib_content(data, zlib.MAX_WBITS, max_bytes=max_bytes)
            except zlib.error:
                return _decode_zlib_content(data, -zlib.MAX_WBITS, max_bytes=max_bytes)
    except (gzip.BadGzipFile, OSError, zlib.error, ValueError):
        return _ensure_decoded_size(data, max_bytes)
    return _ensure_decoded_size(data, max_bytes)


def _decode_zlib_content(data: bytes, wbits: int, *, max_bytes: int | None) -> bytes:
    decoder = zlib.decompressobj(wbits)
    chunks: list[bytes] = []
    total = 0
    for chunk in _iter_bytes_chunks(data):
        pending = chunk
        while pending:
            decoded = decoder.decompress(
                pending,
                _remaining_output_limit(total, max_bytes),
            )
            total = _append_decoded_chunk(chunks, total, decoded, max_bytes)
            pending = decoder.unconsumed_tail
    decoded = decoder.flush(_remaining_output_limit(total, max_bytes))
    _append_decoded_chunk(chunks, total, decoded, max_bytes)
    return b"".join(chunks)


def _iter_bytes_chunks(data: bytes) -> list[bytes]:
    return [
        data[index : index + _RESPONSE_READ_CHUNK_BYTES]
        for index in range(0, len(data), _RESPONSE_READ_CHUNK_BYTES)
    ]


def _remaining_output_limit(total: int, max_bytes: int | None) -> int:
    if max_bytes is None:
        return 0
    return max(1, max_bytes - total + 1)


def _append_decoded_chunk(
    chunks: list[bytes],
    total: int,
    chunk: bytes,
    max_bytes: int | None,
) -> int:
    if not chunk:
        return total
    total += len(chunk)
    if max_bytes is not None and total > max_bytes:
        raise ResponseTooLargeError(f"Decoded response exceeded cap of {max_bytes} bytes")
    chunks.append(chunk)
    return total


def _ensure_decoded_size(data: bytes, max_bytes: int | None) -> bytes:
    if max_bytes is not None and len(data) > max_bytes:
        raise ResponseTooLargeError(f"Decoded response exceeded cap of {max_bytes} bytes")
    return data
