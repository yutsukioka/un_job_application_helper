"""Application-level admission policy for private local API routes."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from job_api.auth import (
    PrivateApiToken,
    load_private_api_token,
    token_source_is_configured,
)

PRIVATE_MODE_ENVIRONMENT = "ATLAS_PRIVATE_API_MODE"
CORS_ORIGINS_ENVIRONMENT = "ATLAS_CORS_ORIGINS"

_PRIVATE_PREFIXES = ("/api/saved-searches", "/api/tracker")
_PRIVATE_EXACT_PATHS = frozenset({"/api/assistant/runs", "/api/sync/run"})
_ACCESS_DENIED = "Private API access denied."
_ACCESS_UNAVAILABLE = "Private API access unavailable."


class PrivateAccessMode(str, Enum):
    LOOPBACK = "loopback"
    TOKEN = "token"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class PrivateAccessPolicy:
    mode: PrivateAccessMode
    token: PrivateApiToken | None
    cors_origins: tuple[str, ...]


def load_private_access_policy(
    environment: Mapping[str, str] | None = None,
) -> PrivateAccessPolicy:
    environment = os.environ if environment is None else environment
    raw_mode = environment.get(
        PRIVATE_MODE_ENVIRONMENT, PrivateAccessMode.LOOPBACK.value
    )
    try:
        mode = PrivateAccessMode(raw_mode)
    except ValueError:
        raise ValueError("Invalid private API configuration.") from None

    token = None
    if mode is PrivateAccessMode.TOKEN:
        token = load_private_api_token(environment)
    elif token_source_is_configured(environment):
        raise ValueError("Invalid private API configuration.")

    return PrivateAccessPolicy(
        mode=mode,
        token=token,
        cors_origins=_parse_cors_origins(environment.get(CORS_ORIGINS_ENVIRONMENT)),
    )


def is_private_endpoint(path: str) -> bool:
    if path in _PRIVATE_EXACT_PATHS:
        return True
    return any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in _PRIVATE_PREFIXES
    )


def is_loopback_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


class PrivateAccessMiddleware:
    def __init__(self, app: ASGIApp, *, policy: PrivateAccessPolicy) -> None:
        self._app = app
        self._policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_private_endpoint(
            _root_relative_route_path(scope)
        ):
            await self._app(scope, receive, send)
            return

        rejection = private_access_rejection(self._policy, scope)
        if rejection is None:
            await self._app(scope, receive, send)
            return
        status_code, detail = rejection
        await JSONResponse({"detail": detail}, status_code=status_code)(
            scope, receive, send
        )


def private_access_rejection(
    policy: PrivateAccessPolicy,
    scope: Scope,
) -> tuple[int, str] | None:
    if policy.mode is PrivateAccessMode.DISABLED:
        return 503, _ACCESS_UNAVAILABLE
    if policy.mode is PrivateAccessMode.LOOPBACK:
        client = scope.get("client")
        if (
            client is not None
            and is_loopback_address(client[0])
            and _has_allowed_loopback_host(scope)
        ):
            return None
        return 403, _ACCESS_DENIED

    authorization_values = Headers(scope=scope).getlist("authorization")
    if (
        len(authorization_values) == 1
        and policy.token is not None
        and policy.token.matches_authorization(authorization_values[0])
    ):
        return None
    return 403, _ACCESS_DENIED


def _root_relative_route_path(scope: Scope) -> str:
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if not root_path or not path.startswith(root_path):
        return path
    if path == root_path:
        return ""
    if path[len(root_path)] == "/":
        return path[len(root_path) :]
    return path


def _has_allowed_loopback_host(scope: Scope) -> bool:
    host_values = Headers(scope=scope).getlist("host")
    return len(host_values) == 1 and _is_allowed_loopback_host(host_values[0])


def _is_allowed_loopback_host(value: str) -> bool:
    if (
        not value
        or value != value.strip()
        or any(character in value for character in "/\\@,\x00")
    ):
        return False

    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return False
        hostname = value[1:closing]
        remainder = value[closing + 1 :]
        if remainder and not _is_valid_host_port(remainder):
            return False
        return is_loopback_address(hostname)

    if value.count(":") > 1:
        return False
    hostname, separator, port = value.rpartition(":")
    if not separator:
        hostname = value
    elif not _is_valid_port(port):
        return False

    if hostname.casefold() in {"localhost", "localhost."}:
        return True
    return is_loopback_address(hostname)


def _is_valid_host_port(remainder: str) -> bool:
    return remainder.startswith(":") and _is_valid_port(remainder[1:])


def _is_valid_port(value: str) -> bool:
    if not value.isascii() or not value.isdecimal():
        return False
    try:
        port = int(value, 10)
    except ValueError:
        return False
    return 1 <= port <= 65535


def _parse_cors_origins(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None or raw_value == "":
        return ()
    origins = raw_value.split(",")
    if any(
        not origin or origin != origin.strip() or origin == "*" for origin in origins
    ):
        raise ValueError("Invalid CORS configuration.")
    if len(set(origins)) != len(origins):
        raise ValueError("Invalid CORS configuration.")
    for origin in origins:
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("Invalid CORS configuration.") from None
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("Invalid CORS configuration.")
    return tuple(origins)
