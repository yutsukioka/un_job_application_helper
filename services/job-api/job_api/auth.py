"""Authentication helpers for LAN-exposed job-api deployments."""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from job_api.config import ApiSettings


AUTH_HEADER = "X-Job-Api-Token"
FAILED_AUTH_LIMIT = 5
FAILED_AUTH_WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class FailedAuthLimiter:
    max_failures: int = FAILED_AUTH_LIMIT
    window_seconds: float = FAILED_AUTH_WINDOW_SECONDS
    _failures: dict[str, list[float]] = field(default_factory=dict)

    def is_limited(self, key: str, *, now: float | None = None) -> bool:
        failures = self._recent_failures(key, now=now)
        return len(failures) >= self.max_failures

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        failures = self._recent_failures(key, now=timestamp)
        failures.append(timestamp)
        self._failures[key] = failures

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def _recent_failures(self, key: str, *, now: float | None = None) -> list[float]:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.window_seconds
        failures = [value for value in self._failures.get(key, []) if value >= cutoff]
        self._failures[key] = failures
        return failures


def require_lan_auth(settings: ApiSettings, limiter: FailedAuthLimiter):
    async def dependency(request: Request) -> None:
        if not settings.allow_lan:
            return
        if not settings.api_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{AUTH_HEADER} is not configured for LAN mode",
            )
        key = _client_key(request)
        if limiter.is_limited(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed authentication attempts",
            )
        provided = request.headers.get(AUTH_HEADER)
        if not _token_matches(provided, settings.api_token):
            limiter.record_failure(key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Missing or invalid {AUTH_HEADER}",
            )
        limiter.reset(key)

    return dependency


def _token_matches(provided: str | None, expected: str) -> bool:
    return hmac.compare_digest((provided or "").encode("utf-8"), expected.encode("utf-8"))


def _client_key(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"
