"""HTTP routes for the sidecar skeleton."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

from .dependencies import (
    DependencyChecker,
    ReadinessReport,
    evaluate_readiness,
    get_dependency_checker,
)
from .metrics import get_registry

router = APIRouter()


@router.get("/ui")
async def ui() -> FileResponse:
    """The chat panel (thin client for /chat)."""
    return FileResponse(STATIC_DIR / "index.html")


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
) -> ReadinessReport:
    """Readiness: 200 iff every dependency is reachable, otherwise 503."""
    report = await evaluate_readiness(checker)
    if report.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
