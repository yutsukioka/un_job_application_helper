from __future__ import annotations

from pathlib import Path

import pytest


SECURITY_TEST_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        try:
            Path(str(item.path)).resolve().relative_to(SECURITY_TEST_ROOT)
        except ValueError:
            continue
        item.add_marker(pytest.mark.security)


@pytest.fixture
def loopback_client():
    """Exercise private validation with a real loopback ASGI peer at the dependency floor."""
    from fastapi.testclient import TestClient

    def make(application):
        async def with_peer(scope, receive, send):
            if scope.get("type") == "http":
                scope = {**scope, "client": ("127.0.0.1", 50123)}
            await application(scope, receive, send)

        return TestClient(with_peer, base_url="http://127.0.0.1")

    return make
