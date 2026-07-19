"""HTTP routes for the sidecar skeleton."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import FileResponse, RedirectResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# The UI pages are embedded in OpenEMR's patient chart as a modal iframe, so
# framing is allowed — but only from the OpenEMR origins we trust (plus local
# dev), never the open web. Override per deployment via COPILOT_FRAME_ANCESTORS.
_DEFAULT_FRAME_ANCESTORS = (
    "'self' https://openemr-early-sub.up.railway.app "
    "http://localhost:8300 https://localhost:9300"
)


def ui_security_headers() -> dict[str, str]:
    ancestors = os.environ.get("COPILOT_FRAME_ANCESTORS", _DEFAULT_FRAME_ANCESTORS)
    return {
        "Content-Security-Policy": f"frame-ancestors {ancestors}",
        # Without this, browsers heuristically reuse cached UI pages after a
        # deploy — users keep running stale HTML/CSS/JS inside the chart
        # modal. no-cache forces revalidation (cheap 304 when unchanged).
        "Cache-Control": "no-cache",
    }

from .dependencies import (
    ComponentReport,
    DependencyChecker,
    ReadinessReport,
    evaluate_readiness,
    get_component_inspector,
    get_dependency_checker,
)
from .metrics import get_registry

router = APIRouter()


@router.get("/")
async def root() -> RedirectResponse:
    """Bare link lands on the chat panel."""
    return RedirectResponse(url="/ui")


@router.get("/ui")
async def ui() -> FileResponse:
    """The chat panel (thin client for /chat)."""
    return FileResponse(STATIC_DIR / "index.html", headers=ui_security_headers())


@router.get("/ui/documents")
async def ui_documents() -> FileResponse:
    """The document workflow panel (thin client for /documents + /ask)."""
    return FileResponse(STATIC_DIR / "documents.html", headers=ui_security_headers())


@router.get("/metrics")
async def metrics() -> dict:
    """PHI-free aggregate metrics for the current process (JSON)."""
    return get_registry().snapshot()


@router.get("/dashboard")
async def dashboard() -> FileResponse:
    """Live observability dashboard rendering /metrics."""
    return FileResponse(STATIC_DIR / "dashboard.html")


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness only: 200 while the process is up. No dependency checks."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    checker: DependencyChecker = Depends(get_dependency_checker),
    inspector: Callable[[], dict[str, ComponentReport]] = Depends(get_component_inspector),
) -> ReadinessReport:
    """Readiness three-state walk.

    ``ready`` (200) — everything ok. ``degraded`` (200) — externals fine but a
    named pipeline component (document store, vector index, reranker) is
    impaired; still serving. ``not_ready`` (503) — an external dependency is
    unreachable; out of rotation.
    """
    report = await evaluate_readiness(checker, inspector)
    if report.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
