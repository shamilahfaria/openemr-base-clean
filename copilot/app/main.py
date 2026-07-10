"""FastAPI application factory for the Clinical Co-Pilot sidecar."""
from __future__ import annotations

from fastapi import FastAPI

from . import chat, wiring
from .middleware import CorrelationIdMiddleware
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Clinical Co-Pilot Sidecar")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(router)
    app.include_router(chat.router)
    # Bind the /chat provider seams to production wiring (tests re-override).
    app.dependency_overrides[chat.get_orchestrator] = wiring.get_orchestrator
    app.dependency_overrides[chat.get_verifier] = wiring.get_verifier
    app.dependency_overrides[chat.get_audit_trail] = wiring.get_audit_trail
    app.dependency_overrides[chat.get_fallback_provider] = wiring.get_fallback_provider
    return app


app = create_app()
