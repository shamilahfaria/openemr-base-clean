"""Langfuse tracing pipeline — the third telemetry backend.

Closes the gap where /ready treated Langfuse as a dependency but nothing was
ever exported to it. A turn becomes one Langfuse trace, keyed by the same
correlation id that ties logs, the dashboard, and the audit entry together.

The trust boundary still holds: only the PHI-free TurnTelemetry crosses to
Langfuse (an external service) — never a patient id, message, or answer. The
SDK itself is isolated behind a sink so these tests never import it.
"""
from __future__ import annotations

import json
import logging

from app import wiring
from app.langfuse_export import (
    LangfuseExporter,
    build_langfuse_sink,
    telemetry_metadata,
)
from app.observability import CompositeExporter, TurnTelemetry


def make_telemetry(**overrides) -> TurnTelemetry:
    values = dict(
        correlation_id="corr-1",
        outcome="verified",
        degraded=False,
        tools_used=["get_medications"],
        verification_passed=True,
        warnings_count=0,
        withheld_count=0,
        latency_ms=123.4,
        model="claude-sonnet-4-5",
        input_tokens=1200,
        output_tokens=180,
        cost_usd=0.0063,
    )
    values.update(overrides)
    return TurnTelemetry(**values)


class FakeSink:
    def __init__(self, fail: bool = False):
        self.traces: list[dict] = []
        self.flushed = 0
        self._fail = fail

    def trace(self, *, id: str, name: str, metadata: dict) -> None:
        if self._fail:
            raise RuntimeError("langfuse network error")
        self.traces.append({"id": id, "name": name, "metadata": metadata})

    def flush(self) -> None:
        self.flushed += 1


def test_exports_turn_as_trace_keyed_by_correlation_id():
    sink = FakeSink()
    LangfuseExporter(sink).export(make_telemetry(correlation_id="corr-9", outcome="fallback"))
    assert len(sink.traces) == 1
    trace = sink.traces[0]
    assert trace["id"] == "corr-9"
    assert trace["metadata"]["outcome"] == "fallback"


def test_trace_metadata_carries_the_full_phi_free_shape():
    metadata = telemetry_metadata(make_telemetry())
    for key in (
        "outcome", "degraded", "tools_used", "verification_passed",
        "warnings_count", "withheld_count", "latency_ms", "model",
        "input_tokens", "output_tokens", "cost_usd",
    ):
        assert key in metadata


def test_trace_never_carries_patient_or_message():
    # TurnTelemetry has no PHI fields; this is a tripwire on the serialized trace.
    serialized = json.dumps(telemetry_metadata(make_telemetry()))
    assert "patient" not in serialized.lower()
    assert "message" not in serialized.lower()


def test_sink_failure_never_raises(caplog):
    with caplog.at_level(logging.WARNING):
        LangfuseExporter(FakeSink(fail=True)).export(make_telemetry())
    assert any("langfuse" in record.getMessage().lower() for record in caplog.records)


def test_build_sink_returns_none_without_both_keys():
    assert build_langfuse_sink({}) is None
    assert build_langfuse_sink({"LANGFUSE_PUBLIC_KEY": "pk"}) is None
    assert build_langfuse_sink({"LANGFUSE_SECRET_KEY": "sk"}) is None


def test_build_sink_builds_when_both_keys_present():
    captured: dict = {}

    def factory(public_key, secret_key, host):
        captured.update(public_key=public_key, secret_key=secret_key, host=host)
        return FakeSink()

    sink = build_langfuse_sink(
        {
            "LANGFUSE_PUBLIC_KEY": "pk-123",
            "LANGFUSE_SECRET_KEY": "sk-456",
            "LANGFUSE_HOST": "https://cloud.langfuse.com",
        },
        sink_factory=factory,
    )
    assert isinstance(sink, FakeSink)
    assert captured == {
        "public_key": "pk-123",
        "secret_key": "sk-456",
        "host": "https://cloud.langfuse.com",
    }


def test_build_sink_degrades_to_none_if_factory_raises(caplog):
    def broken(public_key, secret_key, host):
        raise ImportError("langfuse not installed")

    with caplog.at_level(logging.WARNING):
        sink = build_langfuse_sink(
            {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
            sink_factory=broken,
        )
    assert sink is None


class TestWiring:
    def test_includes_langfuse_backend_when_configured(self, monkeypatch):
        wiring.reset()
        monkeypatch.setattr(wiring, "build_langfuse_sink", lambda: FakeSink())
        exporter = wiring.get_telemetry_exporter()
        assert isinstance(exporter, CompositeExporter)
        names = [type(backend).__name__ for backend in exporter._exporters]
        assert names.count("LangfuseExporter") == 1
        wiring.reset()

    def test_omits_langfuse_backend_when_unconfigured(self, monkeypatch):
        wiring.reset()
        monkeypatch.setattr(wiring, "build_langfuse_sink", lambda: None)
        exporter = wiring.get_telemetry_exporter()
        names = [type(backend).__name__ for backend in exporter._exporters]
        assert "LangfuseExporter" not in names
        assert "LoggingExporter" in names and "MetricsExporter" in names
        wiring.reset()

    def test_flush_telemetry_flushes_the_sink(self, monkeypatch):
        wiring.reset()
        sink = FakeSink()
        monkeypatch.setattr(wiring, "build_langfuse_sink", lambda: sink)
        wiring.get_telemetry_exporter()
        wiring.flush_telemetry()
        assert sink.flushed == 1
        wiring.reset()

    def test_flush_telemetry_is_safe_when_unconfigured(self, monkeypatch):
        wiring.reset()
        monkeypatch.setattr(wiring, "build_langfuse_sink", lambda: None)
        wiring.get_telemetry_exporter()
        wiring.flush_telemetry()  # must not raise
        wiring.reset()
