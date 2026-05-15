"""Adapter base classes and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from jobagg.http import JobAggHTTPClient
from jobagg.models import JobRecord, OrganizationSource
from jobagg.robots import RobotsChecker


@dataclass(slots=True)
class AdapterContext:
    source: OrganizationSource
    http: JobAggHTTPClient
    robots: RobotsChecker | None = None


class JobAdapter(ABC):
    family: ClassVar[str]

    def __init__(self, context: AdapterContext) -> None:
        self.context = context
        self.source = context.source

    def ensure_allowed(self, url: str) -> None:
        if self.context.robots is not None and not self.context.robots.allowed(url):
            raise PermissionError(f"Blocked by robots policy: {url}")

    def fetch_json(self, url: str) -> Any:
        self.ensure_allowed(url)
        return self.context.http.get(url, headers={"Accept": "application/json"}).json()

    def post_json(self, url: str, payload: Any, *, headers: dict[str, str] | None = None) -> Any:
        self.ensure_allowed(url)
        if headers is None:
            return self.context.http.post_json(url, payload).json()
        return self.context.http.post_json(url, payload, headers=headers).json()

    def fetch_text(self, url: str) -> str:
        self.ensure_allowed(url)
        return self.context.http.get(url).text

    def post_form_text(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        self.ensure_allowed(url)
        return self.context.http.post_form(url, payload, headers=headers).text

    @abstractmethod
    def fetch_jobs(self) -> list[JobRecord]:
        """Fetch and normalize jobs for the configured source."""


_REGISTRY: dict[str, type[JobAdapter]] = {}


def register_adapter(adapter_cls: type[JobAdapter]) -> type[JobAdapter]:
    _REGISTRY[adapter_cls.family] = adapter_cls
    return adapter_cls


def get_adapter_class(family: str) -> type[JobAdapter]:
    try:
        return _REGISTRY[family.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"No adapter registered for {family!r}. Available: {available}") from exc


def registered_families() -> list[str]:
    return sorted(_REGISTRY)
