"""Shared fixtures and test doubles for the sidecar-skeleton test suite."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_dependency_checker
from app.main import create_app


class FakeDependencyChecker:
    """Configurable test double for ``DependencyChecker``.

    Each dependency argument accepts:
      * ``True``  -> reachable
      * ``False`` -> unreachable
      * an ``Exception`` instance -> the check raises it (network error / timeout)
    """

    def __init__(self, openemr=True, anthropic=True, langfuse=True):
        self._results = {
            "openemr": openemr,
            "anthropic": anthropic,
            "langfuse": langfuse,
        }

    async def _result(self, name: str) -> bool:
        value = self._results[name]
        if isinstance(value, Exception):
            raise value
        return value

    async def check_openemr(self) -> bool:
        return await self._result("openemr")

    async def check_anthropic(self) -> bool:
        return await self._result("anthropic")

    async def check_langfuse(self) -> bool:
        return await self._result("langfuse")


@pytest.fixture
def anyio_backend():
    # Pin async tests to asyncio (trio is not a dependency).
    return "asyncio"


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False so an unimplemented stub surfaces as an HTTP
    # 500 response (a clean assertion failure) rather than propagating during the
    # Red stage. Assert on status_code before calling .json().
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_ready_client(app):
    """Factory: build a client whose ``/ready`` sees the given dependency states.

    Example: ``make_ready_client(openemr=False)`` or
    ``make_ready_client(langfuse=RuntimeError("boom"))``.
    """

    def _factory(**states) -> TestClient:
        app.dependency_overrides[get_dependency_checker] = lambda: FakeDependencyChecker(**states)
        return TestClient(app, raise_server_exceptions=False)

    yield _factory
    app.dependency_overrides.clear()
