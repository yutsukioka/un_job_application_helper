"""robots.txt and crawl policy helpers."""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


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
    return RobotsPolicy(
        user_agent=str(default.get("user_agent", RobotsPolicy.user_agent)),
        honor_robots_txt=bool(default.get("honor_robots_txt", True)),
        request_timeout_seconds=int(default.get("request_timeout_seconds", 30)),
        min_delay_seconds=float(default.get("min_delay_seconds", 2.0)),
        max_pages_per_source=int(default.get("max_pages_per_source", 25)),
        max_jobs_per_source=int(default.get("max_jobs_per_source", 1000)),
        domains=domains,
    )


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
            except OSError:
                return False
            self._parsers[root] = parser
        return parser.can_fetch(self.policy.user_agent, url)
