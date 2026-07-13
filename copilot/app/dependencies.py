"""Readiness dependency checking.

``/ready`` must validate that meaningful dependencies are reachable (OpenEMR API,
Anthropic, Langfuse). The check is expressed behind ``DependencyChecker`` so the
real implementation does network I/O while tests inject a fake.
"""
from __future__ import annotations

import os
from typing import Protocol

import httpx
from pydantic import BaseModel

from .config import resolve_openemr_urls


class DependencyChecker(Protocol):
    """Reachability checks for the sidecar's external dependencies."""

    async def check_openemr(self) -> bool: ...

    async def check_anthropic(self) -> bool: ...

    async def check_langfuse(self) -> bool: ...


class ReadinessReport(BaseModel):
    status: str                 # "ready" | "not_ready"
    checks: dict[str, str]      # dependency name -> "ok" | "unreachable"


class HttpDependencyChecker:
    """Real checker: probes each dependency's base URL over HTTP.

    A dependency is reachable when its endpoint answers at all (any HTTP status —
    an auth challenge still proves reachability). URLs come from the environment:
      OPENEMR_BASE_URL   (or recovered from OPENEMR_FHIR_BASE_URL; default localhost:8300)
      ANTHROPIC_BASE_URL (default: https://api.anthropic.com)
      LANGFUSE_BASE_URL  (default: https://cloud.langfuse.com)
    """

    def __init__(self, timeout_seconds: float = 3.0):
        self._timeout = timeout_seconds
        openemr_base, _ = resolve_openemr_urls(os.environ)
        self._openemr = openemr_base or "http://localhost:8300"
        self._anthropic = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self._langfuse = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    async def _probe(self, url: str) -> bool:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            await client.get(url)
        return True

    async def check_openemr(self) -> bool:
        return await self._probe(self._openemr)

    async def check_anthropic(self) -> bool:
        return await self._probe(self._anthropic)

    async def check_langfuse(self) -> bool:
        return await self._probe(self._langfuse)


def get_dependency_checker() -> DependencyChecker:
    """FastAPI provider for the checker. Overridden in tests; real impl does I/O."""
    return HttpDependencyChecker()


async def evaluate_readiness(checker: DependencyChecker) -> ReadinessReport:
    """Run every check (a raised exception counts as ``unreachable``) and aggregate.

    ``status`` is ``ready`` only when every dependency reports reachable.
    """
    named_checks = {
        "openemr": checker.check_openemr,
        "anthropic": checker.check_anthropic,
        "langfuse": checker.check_langfuse,
    }

    checks: dict[str, str] = {}
    for name, check in named_checks.items():
        try:
            reachable = await check()
        except Exception:
            reachable = False
        checks[name] = "ok" if reachable else "unreachable"

    all_ok = all(state == "ok" for state in checks.values())
    return ReadinessReport(status="ready" if all_ok else "not_ready", checks=checks)
