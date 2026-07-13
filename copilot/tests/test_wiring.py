"""
Production wiring.

Pins: env config (fail closed on missing required settings), the committed
clinical-rules artifact loads, the tool registry covers the full tool surface,
the fallback provider never returns blank, providers are process singletons,
and create_app binds chat's provider seams to this wiring.
"""
from __future__ import annotations

import pytest

from app import chat, wiring
from app.audit import AuditTrail
from app.config import DEFAULT_MODEL, DEFAULT_RULES_PATH, load_settings
from app.main import create_app
from app.orchestrator import Orchestrator
from app.verifier import Verifier

PATIENT = "uuid-pat-1"
TOKEN = "test-bearer-token-123"

REQUIRED_ENV = {
    "OPENEMR_FHIR_BASE_URL": "https://openemr.example.test/apis/default/fhir",
    "ANTHROPIC_API_KEY": "sk-test-key",
}


class FakeFhirClient:
    def __init__(self, responses: dict | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    async def get(self, path: str, *, bearer_token: str, params: dict | None = None) -> dict:
        self.calls.append((path, bearer_token, params))
        for prefix, value in self._responses.items():
            if path.startswith(prefix):
                return value
        return {"resourceType": "Bundle", "type": "searchset", "entry": []}


class TestSettings:
    def test_missing_fhir_base_url_fails_closed(self):
        with pytest.raises((ValueError, KeyError)):
            load_settings({"ANTHROPIC_API_KEY": "sk-test"})

    def test_missing_anthropic_key_fails_closed(self):
        with pytest.raises((ValueError, KeyError)):
            load_settings({"OPENEMR_FHIR_BASE_URL": "https://x/fhir"})

    def test_required_values_are_read(self):
        settings = load_settings(REQUIRED_ENV)
        assert settings.openemr_fhir_base_url == REQUIRED_ENV["OPENEMR_FHIR_BASE_URL"]
        assert settings.anthropic_api_key == REQUIRED_ENV["ANTHROPIC_API_KEY"]

    def test_defaults_applied(self):
        settings = load_settings(REQUIRED_ENV)
        assert settings.anthropic_model == DEFAULT_MODEL
        assert settings.clinical_rules_path == DEFAULT_RULES_PATH

    def test_overrides_respected(self):
        settings = load_settings(
            {**REQUIRED_ENV, "ANTHROPIC_MODEL": "claude-x", "CLINICAL_RULES_PATH": "other.json"}
        )
        assert settings.anthropic_model == "claude-x"
        assert settings.clinical_rules_path == "other.json"


class TestClinicalRulesArtifact:
    def test_committed_rules_file_loads(self):
        rules = wiring.load_clinical_rules(DEFAULT_RULES_PATH)
        assert rules.version == "2026.07.0"
        assert rules.interactions            # curated, non-empty
        assert rules.dose_limits

    def test_missing_rules_file_fails_closed(self):
        with pytest.raises(OSError):
            wiring.load_clinical_rules("does/not/exist.json")

    def test_malformed_rules_file_fails_closed(self, tmp_path):
        bad = tmp_path / "rules.json"
        bad.write_text("{not json")
        with pytest.raises(ValueError):
            wiring.load_clinical_rules(str(bad))


class TestToolRegistry:
    def test_registry_covers_the_full_tool_surface(self):
        registry = wiring.build_tool_registry(FakeFhirClient())
        assert set(registry.keys()) == set(wiring.TOOL_NAMES)

    @pytest.mark.anyio
    async def test_adapter_passes_patient_and_bearer_through(self):
        client = FakeFhirClient()
        registry = wiring.build_tool_registry(client)
        await registry["get_medications"]({"patient_id": PATIENT}, TOKEN)
        (path, token, params) = client.calls[0]
        assert path.startswith("MedicationRequest")
        assert token == TOKEN
        assert params["patient"] == PATIENT

    @pytest.mark.anyio
    async def test_search_notes_adapter_passes_query(self):
        client = FakeFhirClient(
            {
                "DocumentReference": {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DocumentReference",
                                "id": "note-1",
                                "date": "2026-07-05",
                                "description": "overnight pain escalation",
                            }
                        }
                    ],
                }
            }
        )
        registry = wiring.build_tool_registry(client)
        records = await registry["search_notes"](
            {"patient_id": PATIENT, "query": "pain"}, TOKEN
        )
        assert [r.source_id for r in records] == ["note-1"]


class TestFallbackProvider:
    @pytest.mark.anyio
    async def test_formats_recent_visit_history(self):
        client = FakeFhirClient(
            {
                "Encounter": {
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "Encounter",
                                "id": "enc-1",
                                "period": {"start": "2026-07-01T09:00:00Z"},
                                "type": [{"text": "Hospice inpatient"}],
                            }
                        }
                    ],
                }
            }
        )
        fallback = wiring.build_fallback_provider(client)
        answer = await fallback(PATIENT, TOKEN)
        assert "2026-07-01" in answer
        assert "Hospice inpatient" in answer

    @pytest.mark.anyio
    async def test_no_encounters_still_returns_explicit_text(self):
        fallback = wiring.build_fallback_provider(FakeFhirClient())
        answer = await fallback(PATIENT, TOKEN)
        assert answer.strip()
        assert "no recent visit" in answer.lower()


@pytest.fixture
def wired_env(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    wiring.reset()
    yield
    wiring.reset()


class TestProviders:
    def test_get_verifier_loads_the_committed_rules(self, wired_env):
        verifier = wiring.get_verifier()
        assert isinstance(verifier, Verifier)
        assert verifier._rules.version == "2026.07.0"

    def test_get_audit_trail_is_a_singleton(self, wired_env):
        assert isinstance(wiring.get_audit_trail(), AuditTrail)
        assert wiring.get_audit_trail() is wiring.get_audit_trail()

    def test_get_orchestrator_builds_from_settings(self, wired_env, monkeypatch):
        fake_sdk_client = object()
        monkeypatch.setattr(wiring, "build_anthropic_client", lambda key: fake_sdk_client)
        orchestrator = wiring.get_orchestrator()
        assert isinstance(orchestrator, Orchestrator)
        assert orchestrator._client is fake_sdk_client
        assert set(orchestrator._tools.keys()) == set(wiring.TOOL_NAMES)
        assert orchestrator._model == DEFAULT_MODEL

    def test_get_orchestrator_is_a_singleton(self, wired_env, monkeypatch):
        monkeypatch.setattr(wiring, "build_anthropic_client", lambda key: object())
        assert wiring.get_orchestrator() is wiring.get_orchestrator()


class TestAppBinding:
    def test_create_app_binds_chat_providers_to_production_wiring(self):
        app = create_app()
        overrides = app.dependency_overrides
        assert overrides[chat.get_orchestrator] is wiring.get_orchestrator
        assert overrides[chat.get_verifier] is wiring.get_verifier
        assert overrides[chat.get_audit_trail] is wiring.get_audit_trail
        assert overrides[chat.get_fallback_provider] is wiring.get_fallback_provider
