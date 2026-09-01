"""Account/device abuse controls and secret-free observability."""

from __future__ import annotations

import logging
import math
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_ACCOUNT_REQUEST_LIMIT = 40
DEFAULT_DEVICE_REQUEST_LIMIT = 24
DEFAULT_RATE_WINDOW_SECONDS = 60.0
DEFAULT_MAX_REQUEST_BYTES = 192 * 1024 * 1024
DEFAULT_MAX_ACCOUNTS = 1024
DEFAULT_MAX_CHALLENGES = 4096
DEFAULT_MAX_CHALLENGES_PER_DEVICE = 8
DEFAULT_MAX_SESSIONS = 4096
DEFAULT_MAX_SESSIONS_PER_DEVICE = 8
DEFAULT_MAX_DEVICES_PER_ACCOUNT = 256
MAX_RETAINED_SECURITY_EVENTS = 256

_CATEGORIES = frozenset({"account", "storage", "other"})
_SECURITY_LOGGER = logging.getLogger("atlasvault_api.security")


class RequestRateExceeded(ValueError):
    """Raised when an authenticated account or device exhausts its window."""


@dataclass(frozen=True)
class StoragePrincipal:
    account_id: str
    device_id: str


@dataclass(frozen=True)
class AbuseControlPolicy:
    account_request_limit: int = DEFAULT_ACCOUNT_REQUEST_LIMIT
    device_request_limit: int = DEFAULT_DEVICE_REQUEST_LIMIT
    window_seconds: float = DEFAULT_RATE_WINDOW_SECONDS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_accounts: int = DEFAULT_MAX_ACCOUNTS
    max_challenges: int = DEFAULT_MAX_CHALLENGES
    max_challenges_per_device: int = DEFAULT_MAX_CHALLENGES_PER_DEVICE
    max_sessions: int = DEFAULT_MAX_SESSIONS
    max_sessions_per_device: int = DEFAULT_MAX_SESSIONS_PER_DEVICE
    max_devices_per_account: int = DEFAULT_MAX_DEVICES_PER_ACCOUNT

    def __post_init__(self) -> None:
        integer_limits = (
            self.account_request_limit,
            self.device_request_limit,
            self.max_request_bytes,
            self.max_accounts,
            self.max_challenges,
            self.max_challenges_per_device,
            self.max_sessions,
            self.max_sessions_per_device,
            self.max_devices_per_account,
        )
        valid_window = (
            type(self.window_seconds) in (int, float)
            and not isinstance(self.window_seconds, bool)
            and math.isfinite(self.window_seconds)
            and self.window_seconds > 0
        )
        if (
            any(type(limit) is not int or limit < 1 for limit in integer_limits)
            or not valid_window
        ):
            raise ValueError("invalid abuse-control policy")


@dataclass
class _WindowCounter:
    started_at: float
    count: int = 0


class AccountDeviceRateLimiter:
    """Fixed-window limiter that cannot be reset by rotating a session token."""

    def __init__(
        self,
        policy: AbuseControlPolicy,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        self._policy = policy
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._accounts: dict[str, _WindowCounter] = {}
        self._devices: dict[tuple[str, str], _WindowCounter] = {}
        self._last_observed: float | None = None

    def consume(self, principal: StoragePrincipal) -> None:
        with self._lock:
            now = self._monotonic()
            if not math.isfinite(now):
                raise RequestRateExceeded
            if self._last_observed is not None and now < self._last_observed:
                raise RequestRateExceeded
            self._last_observed = now
            self._prune(now)
            account = self._counter(self._accounts, principal.account_id, now)
            device_key = (principal.account_id, principal.device_id)
            device = self._counter(self._devices, device_key, now)
            if (
                account.count >= self._policy.account_request_limit
                or device.count >= self._policy.device_request_limit
            ):
                raise RequestRateExceeded
            account.count += 1
            device.count += 1

    def _counter(
        self,
        counters: dict[object, _WindowCounter],
        key: object,
        now: float,
    ) -> _WindowCounter:
        counter = counters.get(key)
        if counter is None:
            counter = _WindowCounter(started_at=now)
            counters[key] = counter
        elif now < counter.started_at:
            raise RequestRateExceeded
        return counter

    def _prune(self, now: float) -> None:
        for counters in (self._accounts, self._devices):
            expired = [
                key
                for key, counter in counters.items()
                if now - counter.started_at >= self._policy.window_seconds
            ]
            for key in expired:
                del counters[key]


class SecretFreeTelemetry:
    """Bounded coarse events and counters with no request-derived dimensions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, str | int]] = []
        self._metrics: Counter[tuple[str, str]] = Counter()

    def record(self, category: str, status_code: int) -> None:
        if category not in _CATEGORIES:
            category = "other"
        outcome = _outcome(status_code)
        event: dict[str, str | int] = {
            "category": category,
            "outcome": outcome,
            "status_code": status_code,
        }
        with self._lock:
            self._events.append(event)
            del self._events[:-MAX_RETAINED_SECURITY_EVENTS]
            self._metrics[(category, outcome)] += 1
        _SECURITY_LOGGER.info(
            "atlasvault_request category=%s outcome=%s status=%d",
            category,
            outcome,
            status_code,
        )

    def snapshot(self) -> dict[str, list[dict[str, str | int]]]:
        with self._lock:
            events = [dict(event) for event in self._events]
            metrics = [
                {
                    "category": category,
                    "outcome": outcome,
                    "count": count,
                }
                for (category, outcome), count in sorted(self._metrics.items())
            ]
        return {"events": events, "metrics": metrics}


def _outcome(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "success"
    return {
        400: "invalid",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "too_large",
        422: "invalid",
        429: "rate_limited",
    }.get(status_code, "error")
