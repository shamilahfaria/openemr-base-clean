"""Readiness dependency checking.

``/ready`` walks three states. External dependencies (OpenEMR API, Anthropic,
Langfuse) gate traffic: any unreachable -> ``not_ready`` (503). The in-process
pipeline components — document storage, vector index, reranker — are surfaced
by NAME with their shape; an impaired component -> ``degraded`` (200, still
serving) rather than taking the service out of rotation. Both probes sit
behind providers so the real implementations do I/O / introspection while
tests inject fakes.
"""
from __future__ import annotations

import os
from typing import Callable, Protocol

import httpx
from pydantic import BaseModel

from .config import resolve_openemr_urls


class DependencyChecker(Protocol):
    """Reachability checks for the sidecar's external dependencies."""

    async def check_openemr(self) -> bool: ...

    async def check_anthropic(self) -> bool: ...

    async def check_langfuse(self) -> bool: ...


class ComponentReport(BaseModel):
    """One in-process pipeline component, surfaced by name — never a black box."""

    name: str
    state: str                          # "ok" | "degraded" | "down"
    detail: dict[str, int] = {}


class ReadinessReport(BaseModel):
    status: str                 # "ready" | "degraded" | "not_ready"
    checks: dict[str, str]      # external dependency -> "ok" | "unreachable"
    components: dict[str, ComponentReport] = {}


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


def inspect_components() -> dict[str, ComponentReport]:
    """Introspect the live Week-2 pipeline: store, vector index, reranker.

    Imports are local so readiness introspection can never break app startup;
    a component that fails inspection reports ``down`` rather than raising.
    """
    components: dict[str, ComponentReport] = {}

    try:
        from . import wiring

        store = wiring.get_document_store()
        components["document_store"] = ComponentReport(
            name=type(store).__name__, state="ok", detail=store.stats()
        )
    except Exception:
        components["document_store"] = ComponentReport(name="document_store", state="down")

    try:
        from .rag.retriever import default_retriever

        retriever = default_retriever()
        stats = retriever.stats()
        components["vector_index"] = ComponentReport(
            name=retriever.index_name,
            state="ok" if stats.get("chunks", 0) > 0 else "degraded",
            detail=stats,
        )
        components["reranker"] = ComponentReport(name=retriever.reranker_name, state="ok")
    except Exception:
        components["vector_index"] = ComponentReport(name="vector_index", state="down")
        components["reranker"] = ComponentReport(name="reranker", state="down")

    return components


def get_component_inspector() -> Callable[[], dict[str, ComponentReport]]:
    """FastAPI provider for the component walk. Overridden in tests."""
    return inspect_components


async def evaluate_readiness(
    checker: DependencyChecker,
    inspector: Callable[[], dict[str, ComponentReport]] = inspect_components,
) -> ReadinessReport:
    """Aggregate the three-state walk.

    ``not_ready``  — an external dependency is unreachable (gates traffic).
    ``degraded``   — externals fine, but a pipeline component is impaired
                     (or inspection itself failed); still serving.
    ``ready``      — everything ok.
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

    try:
        components = inspector()
        components_ok = all(c.state == "ok" for c in components.values())
    except Exception:
        components, components_ok = {}, False

    if any(state != "ok" for state in checks.values()):
        status = "not_ready"
    elif not components_ok:
        status = "degraded"
    else:
        status = "ready"
    return ReadinessReport(status=status, checks=checks, components=components)
