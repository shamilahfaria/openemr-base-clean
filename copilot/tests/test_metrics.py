"""Metrics registry + dashboard endpoints.

Failure modes guarded:
  * /metrics leaks PHI (patient id, clinician id, message text) — the
    dashboard is outside the trust boundary and must stay PHI-free.
  * Latency percentiles wrong at the boundaries (empty window, single turn).
  * A failing telemetry backend starves the others (composite fan-out).
  * Rejected requests (401) invisible in request/error counts.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import metrics as metrics_module
from app.main import create_app
from app.metrics import MetricsExporter, MetricsRegistry
from app.observability import CompositeExporter, TurnTelemetry, turn_cost_usd


def make_telemetry(**overrides) -> TurnTelemetry:
    base = dict(
        correlation_id="cid-1",
        outcome="verified",
        degraded=False,
        tools_used=["get_medications"],
        verification_passed=True,
        warnings_count=1,
        withheld_count=2,
        latency_ms=100.0,
        model="test-model",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=turn_cost_usd(1000, 200),
    )
    base.update(overrides)
    return TurnTelemetry(**base)


class TestMetricsRegistry:
    def test_empty_snapshot_has_zeroes_not_errors(self):
        snapshot = MetricsRegistry().snapshot()
        assert snapshot["requests_total"] == 0
        assert snapshot["error_rate"] == 0.0
        assert snapshot["latency_ms"]["p50"] == 0
        assert snapshot["verification"]["pass_rate"] == 0.0

    def test_aggregates_turns_and_requests(self):
        registry = MetricsRegistry()
        registry.record_request(200)
        registry.record_request(401)
        registry.record_turn(make_telemetry(latency_ms=100.0))
        registry.record_turn(
            make_telemetry(
                outcome="fallback",
                degraded=True,
                verification_passed=False,
                latency_ms=300.0,
            )
        )
        snapshot = registry.snapshot()
        assert snapshot["requests_total"] == 2
        assert snapshot["errors_total"] == 1
        assert snapshot["error_rate"] == 0.5
        assert snapshot["turn_outcomes"] == {"verified": 1, "fallback": 1}
        assert snapshot["latency_ms"]["p50"] == 100
        assert snapshot["latency_ms"]["p95"] == 300
        assert snapshot["verification"]["passed"] == 1
        assert snapshot["verification"]["failed"] == 1
        assert snapshot["tokens"] == {"input": 2000, "output": 400}
        assert snapshot["cost_usd_total"] == pytest.approx(
            2 * turn_cost_usd(1000, 200), abs=1e-4
        )
        assert snapshot["tool_calls"] == {"get_medications": 2}

    def test_snapshot_is_phi_free(self):
        registry = MetricsRegistry()
        registry.record_request(200)
        registry.record_turn(make_telemetry())
        # TurnTelemetry has no patient/clinician/message fields by design;
        # the snapshot must not grow any either.
        flat = str(registry.snapshot())
        for forbidden in ("patient", "clinician", "message", "answer"):
            assert forbidden not in flat


class TestCompositeExporter:
    def test_one_failing_backend_does_not_starve_the_rest(self):
        class Boom:
            def export(self, telemetry):
                raise RuntimeError("backend down")

        registry = MetricsRegistry()
        exporter = CompositeExporter([Boom(), MetricsExporter(registry)])
        exporter.export(make_telemetry())  # must not raise
        assert registry.snapshot()["turns_total"] == 1


class TestEndpoints:
    @pytest.fixture(autouse=True)
    def fresh_registry(self):
        metrics_module.reset_registry()
        yield
        metrics_module.reset_registry()

    def test_metrics_endpoint_serves_snapshot(self):
        client = TestClient(create_app())
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "requests_total" in response.json()

    def test_dashboard_serves_html(self):
        client = TestClient(create_app())
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "Observability" in response.text

    def test_rejected_chat_request_is_counted(self):
        client = TestClient(create_app())
        response = client.post("/chat", json={})  # no auth -> 401/422
        assert response.status_code in (401, 422)
        snapshot = client.get("/metrics").json()
        assert snapshot["requests_total"] == 1
        assert snapshot["errors_total"] == 1
