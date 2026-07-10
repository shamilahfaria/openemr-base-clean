"""FastAPI application factory for the Clinical Co-Pilot sidecar."""
from __future__ import annotations

from fastapi import FastAPI

from .chat import router as chat_router
from .middleware import CorrelationIdMiddleware
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Clinical Co-Pilot Sidecar")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(router)
    app.include_router(chat_router)
    return app


app = create_app()
