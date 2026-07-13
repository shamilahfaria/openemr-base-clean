"""Langfuse tracing backend — the durable, multi-instance telemetry sink.

A ``TelemetryExporter`` (same seam as ``LoggingExporter`` / ``MetricsExporter``)
that turns each completed turn into one Langfuse trace, keyed by the turn's
correlation id so a trace lines up with the logs, the dashboard row, and the
audit entry for the same request.

Trust boundary: only the PHI-free ``TurnTelemetry`` is sent. Langfuse is an
external service, so patient ids, messages, prompts, and answers must never
reach it — enforced by construction (TurnTelemetry has no such fields) and by
``test_langfuse_export`` / eval I9.

The SDK is isolated behind ``LangfuseSink`` and imported lazily by the default
factory, so the rest of the app (and the test suite) never depends on it. When
the Langfuse keys are absent the backend is simply not built and telemetry still
flows to logs and the dashboard.
"""
from __future__ import annotations

import logging
from typing import Callable, Mapping, Protocol

from .observability import TurnTelemetry

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://cloud.langfuse.com"


def telemetry_metadata(telemetry: TurnTelemetry) -> dict:
    """The PHI-free trace payload. Model dump is safe because TurnTelemetry
    carries no patient/message fields; correlation_id becomes the trace id."""
    metadata = telemetry.model_dump()
    metadata.pop("correlation_id", None)
    return metadata


class LangfuseSink(Protocol):
    """The minimal surface this module needs from a Langfuse client."""

    def trace(self, *, id: str, name: str, metadata: dict) -> None: ...

    def flush(self) -> None: ...


class LangfuseExporter:
    """TelemetryExporter backend that records one trace per turn.

    Export must never break a request, so a sink failure is logged (PHI-free —
    type name only) and swallowed. The CompositeExporter also isolates backends;
    this is defence in depth.
    """

    def __init__(self, sink: LangfuseSink):
        self._sink = sink

    def export(self, telemetry: TurnTelemetry) -> None:
        try:
            self._sink.trace(
                id=telemetry.correlation_id,
                name="chat_turn",
                metadata=telemetry_metadata(telemetry),
            )
        except Exception as exc:
            logger.warning("langfuse export failed: %s", type(exc).__name__)


class _SdkLangfuseSink:
    """Real backend: wraps the Langfuse SDK. Imported lazily so the SDK is a
    deploy-time dependency only."""

    def __init__(self, public_key: str, secret_key: str, host: str):
        from langfuse import Langfuse  # noqa: PLC0415 — lazy on purpose

        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    def trace(self, *, id: str, name: str, metadata: dict) -> None:
        outcome = str(metadata.get("outcome", "")) or "unknown"
        self._client.trace(id=id, name=name, metadata=metadata, tags=[outcome])

    def flush(self) -> None:
        self._client.flush()


SinkFactory = Callable[[str, str, str], LangfuseSink]


def build_langfuse_sink(
    env: Mapping[str, str] | None = None,
    sink_factory: SinkFactory | None = None,
) -> LangfuseSink | None:
    """Build a sink when both Langfuse keys are configured, else ``None``.

    A factory failure (e.g. the SDK is not installed) degrades to ``None`` so
    the service still starts and telemetry keeps flowing to the other backends.
    """
    import os

    source = os.environ if env is None else env
    public_key = source.get("LANGFUSE_PUBLIC_KEY")
    secret_key = source.get("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        return None

    host = source.get("LANGFUSE_HOST") or source.get("LANGFUSE_BASE_URL") or DEFAULT_HOST
    factory = sink_factory or _default_sink_factory
    try:
        return factory(public_key, secret_key, host)
    except Exception as exc:
        logger.warning("langfuse sink unavailable: %s", type(exc).__name__)
        return None


def _default_sink_factory(public_key: str, secret_key: str, host: str) -> LangfuseSink:
    return _SdkLangfuseSink(public_key, secret_key, host)
