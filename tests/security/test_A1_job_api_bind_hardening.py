from __future__ import annotations

import pytest

from job_api.launcher import load_launch_config


def test_A1_job_api_default_startup_check_remains_loopback():
    assert load_launch_config({}).host == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com"])
def test_A1_job_api_refuses_lan_bind_without_token(host):
    with pytest.raises(ValueError):
        load_launch_config({"ATLAS_API_HOST": host, "ATLAS_ALLOW_LAN": "1"})


def test_A1_job_api_allows_lan_bind_with_explicit_token_hardening():
    config = load_launch_config(
        {
            "ATLAS_API_HOST": "0.0.0.0",
            "ATLAS_ALLOW_LAN": "1",
            "ATLAS_PRIVATE_API_MODE": "token",
            "ATLAS_PRIVATE_API_TOKEN": "a" * 48,
        }
    )
    assert config.host == "0.0.0.0"
