"""Correlation-ID middleware.

Contract (see ARCHITECTURE.md, Component 9 + Request Flow step 3):
  * If the request carries a non-blank ``X-Correlation-ID`` header, reuse it.
  * Otherwise mint a UUID4.
  * Always echo the id on the response ``X-Correlation-ID`` header.
  * Expose the id to the rest of the request via ``get_correlation_id()``.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound = (request.headers.get(CORRELATION_HEADER) or "").strip()
        correlation_id = inbound if inbound else str(uuid.uuid4())

        token = _correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            _correlation_id.reset(token)

        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def get_correlation_id() -> str:
    """Return the correlation id bound to the current request context."""
    return _correlation_id.get()
