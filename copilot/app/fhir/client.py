"""Read-only FHIR client for OpenEMR — STUB (no implementation yet).

Contract (ARCHITECTURE.md: Tools & Contracts; AUDIT.md S5, A-integration):
  * Every request carries the signed-in user's OAuth2 bearer token
    (``Authorization: Bearer <token>``) — the sidecar never mints identity.
  * URLs are built from the configured base URL (OpenEMR's
    ``/apis/default/fhir`` mount) plus a resource path like ``Patient/{id}``.
  * HTTP failures map to typed errors so callers can fail closed:
      401/403            -> FhirAuthError
      404                -> FhirNotFoundError
      5xx / network / timeout -> FhirUnavailableError
  * Response bodies from OpenEMR are never embedded in exception messages
    (they may carry PHI or SQL/debug output — AUDIT.md S4).
"""
from __future__ import annotations

import httpx


class FhirError(Exception):
    """Base class for FHIR access failures."""


class FhirAuthError(FhirError):
    """Token missing/expired/insufficient (HTTP 401/403). Fail closed."""


class FhirNotFoundError(FhirError):
    """Resource does not exist (HTTP 404)."""


class FhirUnavailableError(FhirError):
    """OpenEMR unreachable or erroring (5xx, network error, timeout)."""


class FhirClient:
    """Thin async wrapper over httpx for OpenEMR FHIR reads."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport  # injectable for tests (httpx.MockTransport)

    async def get(
        self,
        path: str,
        *,
        bearer_token: str,
        params: dict | None = None,
    ) -> dict:
        """GET ``{base_url}/{path}`` and return the parsed JSON body."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {bearer_token}"}

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            # Deliberately generic: no upstream detail, no PHI (AUDIT S4).
            raise FhirUnavailableError("FHIR endpoint unreachable") from exc

        status = response.status_code
        if status in (401, 403):
            raise FhirAuthError(f"FHIR request unauthorized (HTTP {status})")
        if status == 404:
            raise FhirNotFoundError("FHIR resource not found (HTTP 404)")
        if status >= 500:
            # Never include the response body — OpenEMR error pages can carry
            # PHI or raw SQL (AUDIT S4).
            raise FhirUnavailableError(f"FHIR server error (HTTP {status})")
        if status >= 400:
            raise FhirError(f"FHIR request failed (HTTP {status})")

        return response.json()
