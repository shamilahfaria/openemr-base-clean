"""
TDD (Red) suite — build step 10: observability.

Made concrete by the live smoke test: an invalid Anthropic key produced a
silent fallback with zero server-side trace. Pins: PHI-free telemetry exported
on every turn outcome, export failures never break a request, and orchestrator
exceptions are logged with the correlation id (and without secrets).
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app import chat, wiring
from app.audit import AuditTrail
from app.main import create_app
from app.observability import CompositeExporter, LoggingExporter, TurnTelemetry
from app.orchestrator import TurnDraft
from app.tools.chart import MedicationRecord
from app.verifier import ClinicalRuleSet, Verifier

PATIENT = "uuid-pat-1"
TOKEN = "test-bearer-token-123"
CLINICIAN = "nurse-maria"
MESSAGE = "how is her pain this morning?"

MORPHINE = MedicationRecord(
    source_id="med-1", name="Morphine sulfate", dose="5 mg", route="IV",
    sig="5 mg IV q4h PRN", is_prn=True, prn_interval="Q4H", status="active",
)

VERIFIED_DRAFT = TurnDraft(
    answer="She is on morphine 5 mg [src: med-1].",
    retrieved=[MORPHINE],
    tools_used=["get_medications"],
)

UNCITED_DRAFT = TurnDraft(
    answer="Her prognosis is weeks to months.",
    retrieved=[MORPHINE],
    tools_used=["get_medications"],
)


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
    )
    values.update(overrides)
    return TurnTelemetry(**values)


class CapturingExporter:
    def __init__(self):
        self.exported: list[TurnTelemetry] = []

    def export(self, telemetry: TurnTelemetry) -> None:
        self.exported.append(telemetry)


class BrokenExporter:
    def export(self, telemetry: TurnTelemetry) -> None:
        raise RuntimeError("langfuse is down")


class FakeOrchestrator:
    def __init__(self, result: object):
        self._result = result

    async def run_turn(self, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeFallback:
    def __init__(self, result: object = "Recent visit history: ..."):
        self._result = result

    async def __call__(self, patient_id: str, bearer_token: str) -> str:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def harness():
    app = create_app()
    state = {
        "orchestrator": FakeOrchestrator(VERIFIED_DRAFT),
        "fallback": FakeFallback(),
        "exporter": CapturingExporter(),
    }
    app.dependency_overrides[chat.get_orchestrator] = lambda: state["orchestrator"]
    app.dependency_overrides[chat.get_verifier] = lambda: Verifier(
        ClinicalRuleSet(version="test-rules")
    )
    app.dependency_overrides[chat.get_audit_trail] = lambda: AuditTrail()
    app.dependency_overrides[chat.get_fallback_provider] = lambda: state["fallback"]
    app.dependency_overrides[chat.get_telemetry_exporter] = lambda: state["exporter"]
    client = TestClient(app, raise_server_exceptions=False)
    return client, state


def post_chat(client):
    return client.post(
        "/chat",
        json={"patient_id": PATIENT, "message": MESSAGE, "session_id": "session-1"},
        headers={"Authorization": f"Bearer {TOKEN}", "X-Clinician-Id": CLINICIAN},
    )


class TestTelemetryModel:
    def test_model_cannot_carry_patient_or_message(self):
        # PHI-free by construction — tripwire against future field additions.
        assert "patient_id" not in TurnTelemetry.model_fields
        assert "message" not in TurnTelemetry.model_fields

    def test_logging_exporter_emits_one_info_line(self, caplog):
        with caplog.at_level(logging.INFO):
            LoggingExporter().export(make_telemetry())
        assert any(
            "corr-1" in record.getMessage() and "verified" in record.getMessage()
            for record in caplog.records
        )


class TestTurnTelemetryEmission:
    def test_verified_turn_exports_telemetry(self, harness):
        client, state = harness
        response = post_chat(client)
        assert response.status_code == 200
        (telemetry,) = state["exporter"].exported
        assert telemetry.outcome == "verified"
        assert telemetry.degraded is False
        assert telemetry.verification_passed is True
        assert telemetry.tools_used == ["get_medications"]
        assert telemetry.latency_ms >= 0
        assert telemetry.correlation_id == response.json()["correlation_id"]

    def test_fallback_turn_exports_telemetry(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(UNCITED_DRAFT)
        post_chat(client)
        (telemetry,) = state["exporter"].exported
        assert telemetry.outcome == "fallback"
        assert telemetry.degraded is True
        assert telemetry.verification_passed is False
        assert telemetry.withheld_count == 1

    def test_total_failure_exports_denied_telemetry(self, harness):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(RuntimeError("down"))
        state["fallback"] = FakeFallback(RuntimeError("also down"))
        response = post_chat(client)
        assert response.status_code == 503
        (telemetry,) = state["exporter"].exported
        assert telemetry.outcome == "denied"

    def test_exported_telemetry_is_phi_free(self, harness):
        client, state = harness
        post_chat(client)
        serialized = state["exporter"].exported[0].model_dump_json()
        assert PATIENT not in serialized
        assert MESSAGE not in serialized
        assert TOKEN not in serialized

    def test_broken_exporter_never_breaks_the_request(self, harness, caplog):
        client, state = harness
        state["exporter"] = BrokenExporter()
        with caplog.at_level(logging.WARNING):
            response = post_chat(client)
        assert response.status_code == 200
        assert any("export" in r.getMessage().lower() for r in caplog.records)


class TestErrorLogging:
    def test_orchestrator_error_is_logged_with_correlation_id(self, harness, caplog):
        client, state = harness
        state["orchestrator"] = FakeOrchestrator(RuntimeError("anthropic down"))
        with caplog.at_level(logging.ERROR):
            response = post_chat(client)
        correlation_id = response.json()["correlation_id"]
        error_records = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR and correlation_id in r.getMessage()
        ]
        assert error_records, "expected an ERROR log carrying the correlation id"
        # secrets and PHI stay out of the log line
        message = error_records[0].getMessage()
        assert TOKEN not in message
        assert MESSAGE not in message


class TestWiringBinding:
    def test_create_app_binds_telemetry_exporter(self):
        app = create_app()
        assert (
            app.dependency_overrides[chat.get_telemetry_exporter]
            is wiring.get_telemetry_exporter
        )

    def test_wiring_provides_a_composite_exporter_singleton(self):
        # Every turn reaches BOTH the structured log and the /metrics registry.
        wiring.reset()
        exporter = wiring.get_telemetry_exporter()
        assert isinstance(exporter, CompositeExporter)
        backends = [type(backend).__name__ for backend in exporter._exporters]
        assert "LoggingExporter" in backends
        assert "MetricsExporter" in backends
        assert wiring.get_telemetry_exporter() is exporter
        wiring.reset()
