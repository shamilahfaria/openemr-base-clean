"""HTTP routes for the sidecar skeleton."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from .dependencies import (
    DependencyChecker,
    ReadinessReport,
    evaluate_readiness,
    get_dependency_checker,
)

router = APIRouter()


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
