"""robots.txt and crawl policy helpers."""

from __future__ import annotations

import logging
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RobotsPolicy:
    user_agent: str = "jobagg/0.1"
    honor_robots_txt: bool = True
    request_timeout_seconds: int = 30
    min_delay_seconds: float = 2.0
    max_pages_per_source: int = 25
    max_jobs_per_source: int = 1000
    domains: dict[str, dict[str, Any]] = field(default_factory=dict)

    def domain_config_for(self, host: str) -> dict[str, Any]:
        override = self.domains.get(host.lower())
        if override is None:
            override = self.domains.get(host.lower().split(":", 1)[0])
        return override or {}

    def honor_robots_for(self, host: str) -> bool:
        override = self.domain_config_for(host)
        return bool(override.get("honor_robots_txt", self.honor_robots_txt))

    def min_delay_for(self, host: str) -> float:
        override = self.domain_config_for(host)
        return float(override.get("min_delay_seconds", self.min_delay_seconds))


def load_policy(path: str | Path | None = None) -> RobotsPolicy:
    if path is None:
        return RobotsPolicy()
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load robots policy YAML") from exc

    data = yaml.safe_load(Path(path).read_text()) or {}
    default = data.get("default") or {}
    domains = {
        str(host).lower(): dict(config or {})
        for host, config in (data.get("domains") or {}).items()
    }
    policy = RobotsPolicy(
        user_agent=str(default.get("user_agent", RobotsPolicy.user_agent)),
        honor_robots_txt=bool(default.get("honor_robots_txt", True)),
        request_timeout_seconds=int(default.get("request_timeout_seconds", 30)),
        min_delay_seconds=float(default.get("min_delay_seconds", 2.0)),
        max_pages_per_source=int(default.get("max_pages_per_source", 25)),
        max_jobs_per_source=int(default.get("max_jobs_per_source", 1000)),
        domains=domains,
    )
    for warning in validate_policy(policy):
        _LOGGER.warning("robots policy: %s", warning)
    return policy


def validate_policy(policy: RobotsPolicy) -> list[str]:
    """Return human-readable warnings for risky robots-policy configurations.

    Currently flags any domain (or the global default) that disables
    ``honor_robots_txt`` without recording an ``override_reason``. Operators
    can use these warnings to keep an audit trail of why scrapes proceed in
    the face of a blanket ``Disallow`` from a portal.
    """

    warnings: list[str] = []
    if not policy.honor_robots_txt and not getattr(policy, "_default_override_reason", None):
        # Default override is recorded separately; we leave the global default
        # alone here unless the YAML loader populated an attribute.
        warnings.append(
            "default policy disables honor_robots_txt without override_reason"
        )
    for host, config in policy.domains.items():
        honors = config.get("honor_robots_txt", True)
        if honors:
            continue
        reason = str(config.get("override_reason") or "").strip()
        if not reason:
            warnings.append(
                f"domain {host!r} disables honor_robots_txt without override_reason"
            )
    return warnings


class RobotsChecker:
    def __init__(self, policy: RobotsPolicy) -> None:
        self.policy = policy
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if not self.policy.honor_robots_for(parts.netloc):
            return True
        root = f"{parts.scheme}://{parts.netloc}"
        parser = self._parsers.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(root, "/robots.txt"))
            try:
                parser.read()
            except OSError as exc:
                # A network failure fetching robots.txt is not the same as a
                # disallow rule. Logging and permitting the request avoids
                # silently aborting a sync because the portal hiccupped, but
                # we still record the event so operators can investigate.
                _LOGGER.warning(
                    "Could not read robots.txt at %s (%s); allowing request",
                    urljoin(root, "/robots.txt"),
                    exc,
                )
                # Cache an empty parser so we don't retry on every URL of the
                # same host within this run; an empty parser permits all paths.
                self._parsers[root] = parser
                return True
            self._parsers[root] = parser
        return parser.can_fetch(self.policy.user_agent, url)
