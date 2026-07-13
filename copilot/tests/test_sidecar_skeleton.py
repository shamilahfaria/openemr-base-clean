"""Sidecar skeleton endpoints: root, liveness, readiness, correlation id.

Covers cross-boundary concerns from ARCHITECTURE.md (Components 2, 3, 9):

  * GET /         — redirects to the chat panel (/ui) so the bare link works.
  * GET /health   — liveness only, no dependency checks.
  * GET /ready    — validates meaningful dependencies: 200 iff OpenEMR +
                    Anthropic + Langfuse are all reachable, else 503.
  * correlation ID — honor an inbound X-Correlation-ID, else mint one, and
                    always echo it on the response.

Contract:
  * /health  200  -> {"status": "ok"}
  * /ready   200  -> {"status": "ready",     "checks": {dep: "ok"}}
  * /ready   503  -> {"status": "not_ready", "checks": {dep: "ok" | "unreachable"}}
  * response header name: X-Correlation-ID (see app.middleware.CORRELATION_HEADER)
"""
from __future__ import annotations

import uuid

from app.middleware import CORRELATION_HEADER

DEPENDENCIES = ("openemr", "anthropic", "langfuse")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class TestRootRedirect:
    """GET / — the bare link lands on the chat panel."""

    def test_root_redirects_to_ui(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code in (307, 308)
        assert response.headers["location"] == "/ui"

    def test_root_followed_lands_on_ui(self, client):
        assert client.get("/").status_code == 200


class TestHealthEndpoint:
    """GET /health — liveness only, independent of dependencies."""

    def test_health_returns_200_when_process_is_alive(self, client):
        assert client.get("/health").status_code == 200

    def test_health_body_reports_status_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_health_stays_200_even_when_every_dependency_is_down(self, make_ready_client):
        # /health must not perform dependency checks, so a fully-degraded backend
        # does not affect liveness.
        client = make_ready_client(openemr=False, anthropic=False, langfuse=False)
        assert client.get("/health").status_code == 200


class TestReadinessEndpoint:
    """GET /ready — 200 iff all dependencies reachable, else 503."""

    def test_ready_returns_200_when_all_dependencies_reachable(self, make_ready_client):
        client = make_ready_client()  # all reachable by default
        assert client.get("/ready").status_code == 200

    def test_ready_body_reports_every_dependency_ok(self, make_ready_client):
        response = make_ready_client().get("/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "checks": {"openemr": "ok", "anthropic": "ok", "langfuse": "ok"},
        }

    def test_ready_returns_503_when_openemr_unreachable(self, make_ready_client):
        response = make_ready_client(openemr=False).get("/ready")
        assert response.status_code == 503

    def test_ready_returns_503_when_anthropic_unreachable(self, make_ready_client):
        response = make_ready_client(anthropic=False).get("/ready")
        assert response.status_code == 503

    def test_ready_returns_503_when_langfuse_unreachable(self, make_ready_client):
        # Langfuse is a hard dependency for readiness: any dep down -> 503.
        response = make_ready_client(langfuse=False).get("/ready")
        assert response.status_code == 503

    def test_ready_status_field_is_not_ready_on_failure(self, make_ready_client):
        response = make_ready_client(openemr=False).get("/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"

    def test_ready_flags_only_the_failed_dependency_as_unreachable(self, make_ready_client):
        response = make_ready_client(anthropic=False).get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {
            "openemr": "ok",
            "anthropic": "unreachable",
            "langfuse": "ok",
        }

    def test_ready_flags_all_failed_dependencies_when_multiple_down(self, make_ready_client):
        response = make_ready_client(openemr=False, langfuse=False).get("/ready")
        assert response.status_code == 503
        checks = response.json()["checks"]
        assert checks["openemr"] == "unreachable"
        assert checks["langfuse"] == "unreachable"
        assert checks["anthropic"] == "ok"

    def test_ready_reports_all_checks_present_in_body(self, make_ready_client):
        checks = make_ready_client().get("/ready").json()["checks"]
        assert set(checks.keys()) == set(DEPENDENCIES)

    # --- error handling -------------------------------------------------------

    def test_ready_treats_a_raising_check_as_unreachable_not_500(self, make_ready_client):
        # A dependency check that throws (network error/timeout) must be caught
        # and reported as unreachable — the endpoint returns 503, never a 500.
        response = make_ready_client(openemr=RuntimeError("connection refused")).get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["openemr"] == "unreachable"

    def test_ready_returns_503_when_all_checks_raise(self, make_ready_client):
        response = make_ready_client(
            openemr=TimeoutError(),
            anthropic=RuntimeError(),
            langfuse=ConnectionError(),
        ).get("/ready")
        assert response.status_code == 503
        assert all(v == "unreachable" for v in response.json()["checks"].values())


class TestCorrelationIdMiddleware:
    """Correlation ID: honor inbound X-Correlation-ID, else mint; always echo."""

    def test_response_includes_correlation_id_header(self, client):
        response = client.get("/health")
        assert CORRELATION_HEADER in response.headers

    def test_minted_correlation_id_is_a_valid_uuid(self, client):
        response = client.get("/health")
        assert CORRELATION_HEADER in response.headers
        assert _is_uuid(response.headers[CORRELATION_HEADER])

    def test_honors_inbound_correlation_id(self, client):
        inbound = "trace-abc-123"
        response = client.get("/health", headers={CORRELATION_HEADER: inbound})
        assert CORRELATION_HEADER in response.headers
        assert response.headers[CORRELATION_HEADER] == inbound

    def test_mints_new_id_when_inbound_header_absent(self, client):
        response = client.get("/health")
        assert CORRELATION_HEADER in response.headers
        assert response.headers[CORRELATION_HEADER]  # non-empty

    def test_mints_new_id_when_inbound_header_is_empty(self, client):
        response = client.get("/health", headers={CORRELATION_HEADER: ""})
        assert CORRELATION_HEADER in response.headers
        value = response.headers[CORRELATION_HEADER]
        assert value  # must not echo an empty id
        assert _is_uuid(value)

    def test_mints_new_id_when_inbound_header_is_whitespace(self, client):
        response = client.get("/health", headers={CORRELATION_HEADER: "   "})
        assert CORRELATION_HEADER in response.headers
        assert _is_uuid(response.headers[CORRELATION_HEADER])

    def test_each_request_without_inbound_gets_a_unique_id(self, client):
        first = client.get("/health")
        second = client.get("/health")
        assert first.headers[CORRELATION_HEADER] != second.headers[CORRELATION_HEADER]

    def test_correlation_id_present_on_ready_success(self, make_ready_client):
        response = make_ready_client().get("/ready")
        assert CORRELATION_HEADER in response.headers

    def test_correlation_id_present_even_on_503_response(self, make_ready_client):
        # Failure responses must still be traceable.
        response = make_ready_client(openemr=False).get("/ready")
        assert response.status_code == 503
        assert CORRELATION_HEADER in response.headers


class TestChatUi:
    def test_ui_serves_the_chat_panel(self, client):
        response = client.get("/ui")
        assert response.status_code == 200
        assert "Clinical Co-Pilot" in response.text
        assert "/chat" in response.text  # the panel talks to the agent endpoint

    def test_ui_collects_the_required_request_context(self, client):
        # bearer token, patient id, clinician id — everything /chat requires
        text = client.get("/ui").text
        for field_id in ("token", "patient", "clinician"):
            assert f'id="{field_id}"' in text
