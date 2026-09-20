"""SSRF guardrails for crawler HTTP requests."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable, Iterable, Any
from urllib.parse import urljoin, urlsplit


class SSRFProtectionError(ValueError):
    """Raised when a URL violates crawler network safety policy."""


Resolver = Callable[[str], Iterable[str]]


DENIED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "169.254.169.254/32",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


@dataclass(frozen=True, slots=True)
class ValidatedEndpoint:
    """Request-local numeric destinations; never resolve the hostname again to connect."""

    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(slots=True)
class SafeHTTPPolicy:
    allowed_hosts: set[str] = field(default_factory=set)
    resolver: Resolver | None = None
    max_redirects: int = 5

    def __post_init__(self) -> None:
        self.allowed_hosts = {_normalize_host(host) for host in self.allowed_hosts if host}

    def validate_url(self, url: str) -> str:
        return self.resolve_url(url).host

    def resolve_url(self, url: str) -> ValidatedEndpoint:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise SSRFProtectionError("URL scheme is not allowed")
        if not parsed.hostname:
            raise SSRFProtectionError("URL host is required")
        host = _normalize_host(parsed.hostname)
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise SSRFProtectionError("URL host is not in the organization allowlist")
        if parsed.username is not None or parsed.password is not None:
            raise SSRFProtectionError("URL credentials are not allowed")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise SSRFProtectionError("URL port is invalid") from exc
        addresses = tuple(dict.fromkeys(self._resolve_host(host)))
        if not addresses:
            raise SSRFProtectionError("URL host could not be resolved")
        for address in addresses:
            if _is_denied_address(address):
                raise SSRFProtectionError("URL resolves to a denied network")
            if "%" in address:
                raise SSRFProtectionError("Scoped network addresses are not allowed")
        return ValidatedEndpoint(host, port, addresses)

    def validate_redirect(self, from_url: str, location: str, *, redirect_count: int) -> str:
        if redirect_count > self.max_redirects:
            raise SSRFProtectionError("Too many redirects")
        return safe_urljoin(from_url, location, policy=self)

    def _resolve_host(self, host: str) -> list[str]:
        if _as_ip_address(host) is not None:
            return [host]
        resolver = self.resolver or _resolve_with_socket
        return list(resolver(host))


def safe_urljoin(base_url: str, url: str, *, policy: SafeHTTPPolicy) -> str:
    joined = urljoin(base_url, url)
    policy.validate_url(joined)
    return joined


def allowed_hosts_for_source(source: Any) -> set[str]:
    hosts: set[str] = set()
    _add_url_host(hosts, getattr(source, "base_url", ""))
    for value in _walk_values(getattr(source, "extra", {}) or {}):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            _add_url_host(hosts, value)
    return hosts


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


def _add_url_host(hosts: set[str], url: str) -> None:
    host = urlsplit(str(url)).hostname
    if host:
        hosts.add(_normalize_host(host))


def _resolve_with_socket(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})


def _normalize_host(host: str) -> str:
    return host.strip().strip("[]").casefold()


def _is_denied_address(value: str) -> bool:
    address = _as_ip_address(value)
    if address is None:
        raise SSRFProtectionError("URL host could not be resolved")
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in DENIED_NETWORKS)


def _as_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None
