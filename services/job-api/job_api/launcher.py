"""Validated process launcher for the local job API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from job_api.private_access import (
    PrivateAccessMode,
    PrivateAccessPolicy,
    is_loopback_address,
    load_private_access_policy,
)

HOST_ENVIRONMENT = "ATLAS_API_HOST"
PORT_ENVIRONMENT = "ATLAS_API_PORT"
ALLOW_LAN_ENVIRONMENT = "ATLAS_ALLOW_LAN"
TRUST_PROXY_ENVIRONMENT = "ATLAS_TRUST_PROXY_HEADERS"


@dataclass(frozen=True, slots=True)
class LaunchConfig:
    host: str
    port: int
    private_access: PrivateAccessPolicy


def load_launch_config(environment: Mapping[str, str] | None = None) -> LaunchConfig:
    environment = os.environ if environment is None else environment
    policy = load_private_access_policy(environment)
    host = environment.get(HOST_ENVIRONMENT, "127.0.0.1")
    raw_port = environment.get(PORT_ENVIRONMENT, "8765")
    allow_lan = environment.get(ALLOW_LAN_ENVIRONMENT, "0")
    trust_proxy = environment.get(TRUST_PROXY_ENVIRONMENT)

    try:
        port = int(raw_port, 10)
    except ValueError:
        raise ValueError("Invalid API launch configuration.") from None
    if (
        not host
        or host != host.strip()
        or not 1 <= port <= 65535
        or allow_lan not in {"0", "1"}
        or trust_proxy is not None
    ):
        raise ValueError("Invalid API launch configuration.")

    if not is_loopback_address(host) and (
        allow_lan != "1" or policy.mode is not PrivateAccessMode.TOKEN
    ):
        raise ValueError("Invalid API launch configuration.")

    return LaunchConfig(host=host, port=port, private_access=policy)


def main() -> None:
    import uvicorn

    config = load_launch_config()
    uvicorn.run(
        "job_api.app:app",
        host=config.host,
        port=config.port,
        reload=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
