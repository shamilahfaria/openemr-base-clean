"""
The read-only FHIR client.

Contract under test (app/fhir/client.py):
  * bearer token forwarded as ``Authorization: Bearer <token>``
  * URL = base_url + "/" + resource path; query params passed through
  * 200 -> parsed JSON body
  * 401/403 -> FhirAuthError (fail closed)
  * 404     -> FhirNotFoundError
  * 5xx / network error / timeout -> FhirUnavailableError
  * upstream response bodies never leak into exception messages (AUDIT S4 —
    OpenEMR error pages can contain PHI/SQL)

Transport is mocked with httpx.MockTransport — no network, no new deps.
"""
from __future__ import annotations

import httpx
import pytest

from app.fhir.client import (
    FhirAuthError,
    FhirClient,
    FhirNotFoundError,
    FhirUnavailableError,
)

BASE_URL = "https://openemr.example.test/apis/default/fhir"
TOKEN = "test-bearer-token-123"


def make_client(handler) -> FhirClient:
    """FhirClient wired to an in-memory transport driven by ``handler``."""
    return FhirClient(BASE_URL, transport=httpx.MockTransport(handler))


def json_response(status_code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=body if body is not None else {})


class TestRequestConstruction:
    @pytest.mark.anyio
    async def test_sends_bearer_token_in_authorization_header(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return json_response(200, {"resourceType": "Patient", "id": "p1"})

        await make_client(handler).get("Patient/p1", bearer_token=TOKEN)
        assert seen["auth"] == f"Bearer {TOKEN}"

    @pytest.mark.anyio
    async def test_builds_url_from_base_url_and_path(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return json_response(200)

        await make_client(handler).get("Patient/p1", bearer_token=TOKEN)
        assert seen["url"].startswith(BASE_URL)
        assert seen["url"].endswith("/Patient/p1")

    @pytest.mark.anyio
    async def test_passes_query_params_through(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return json_response(200)

        await make_client(handler).get(
            "Condition", bearer_token=TOKEN, params={"patient": "p1"}
        )
        assert seen["params"] == {"patient": "p1"}

    @pytest.mark.anyio
    async def test_returns_parsed_json_body_on_200(self):
        body = {"resourceType": "Patient", "id": "p1", "gender": "female"}

        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, body)

        result = await make_client(handler).get("Patient/p1", bearer_token=TOKEN)
        assert result == body


class TestErrorMapping:
    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures_raise_fhir_auth_error(self, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(status)

        with pytest.raises(FhirAuthError):
            await make_client(handler).get("Patient/p1", bearer_token=TOKEN)

    @pytest.mark.anyio
    async def test_404_raises_fhir_not_found_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(404)

        with pytest.raises(FhirNotFoundError):
            await make_client(handler).get("Patient/nope", bearer_token=TOKEN)

    @pytest.mark.anyio
    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_server_errors_raise_fhir_unavailable_error(self, status):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(status)

        with pytest.raises(FhirUnavailableError):
            await make_client(handler).get("Patient/p1", bearer_token=TOKEN)

    @pytest.mark.anyio
    async def test_network_error_raises_fhir_unavailable_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(FhirUnavailableError):
            await make_client(handler).get("Patient/p1", bearer_token=TOKEN)

    @pytest.mark.anyio
    async def test_timeout_raises_fhir_unavailable_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(FhirUnavailableError):
            await make_client(handler).get("Patient/p1", bearer_token=TOKEN)

    @pytest.mark.anyio
    async def test_upstream_error_body_never_leaks_into_exception_message(self):
        # OpenEMR error pages can contain PHI or raw SQL (AUDIT S4). The typed
        # error must not carry the upstream body text.
        leaked = "SELECT ssn FROM patient_data WHERE pid=42 -- Jane Doe 555-11-1111"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text=leaked)

        with pytest.raises(FhirUnavailableError) as exc_info:
            await make_client(handler).get("Patient/p1", bearer_token=TOKEN)
        assert leaked not in str(exc_info.value)
        assert "Jane Doe" not in str(exc_info.value)
