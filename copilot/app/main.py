"""FastAPI application factory for the Clinical Co-Pilot sidecar."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from . import ask as ask_module
from . import chat, wiring
from .auth_pkce import router as auth_pkce_router
from .demo_token import router as demo_token_router
from .documents import routes as doc_routes
from .metrics import get_registry
from .middleware import CorrelationIdMiddleware
from .routes import router


def configure_logging() -> None:
    """Make app INFO logs (incl. turn_telemetry lines) reach stdout.

    Uvicorn configures handlers only for its own loggers; without a root
    handler the structured telemetry lines are dropped silently.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    root.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Flush any buffered Langfuse traces so a graceful shutdown loses nothing.
    wiring.flush_telemetry()


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Clinical Co-Pilot Sidecar", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)

    @app.middleware("http")
    async def count_chat_requests(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/chat":
            get_registry().record_request(response.status_code)
        return response

    app.include_router(router)
    app.include_router(chat.router)
    app.include_router(demo_token_router)
    app.include_router(auth_pkce_router)
    app.include_router(doc_routes.router)
    app.include_router(ask_module.router)
    # Bind the /chat provider seams to production wiring (tests re-override).
    app.dependency_overrides[chat.get_orchestrator] = wiring.get_orchestrator
    app.dependency_overrides[chat.get_verifier] = wiring.get_verifier
    app.dependency_overrides[chat.get_audit_trail] = wiring.get_audit_trail
    app.dependency_overrides[chat.get_fallback_provider] = wiring.get_fallback_provider
    app.dependency_overrides[chat.get_telemetry_exporter] = wiring.get_telemetry_exporter
    app.dependency_overrides[doc_routes.get_document_extractor] = wiring.get_document_extractor
    app.dependency_overrides[doc_routes.get_document_store] = wiring.get_document_store
    return app


app = create_app()
