"""POST /demo/token — one-click OAuth token for DEMO environments only.

The sidecar passes the caller's bearer straight through to OpenEMR's FHIR API,
so a real, FHIR-scoped OpenEMR access token is required to use /chat. Getting
one normally means running the OAuth password-grant by hand. This endpoint does
that single call server-side so the Co-Pilot UI can offer a "Generate demo
token" button instead.

Safety rails (this is a demo convenience, not a production auth path):
  * OFF by default — 404 unless ``DEMO_TOKEN_ENABLED`` is truthy.
  * Fail closed — missing client/creds returns 503, never a half-configured call.
  * Leak-free — the configured client secret and OpenEMR's raw error body never
    appear in a response (AUDIT.md S4). Only the minted access token is returned.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .config import resolve_openemr_urls

logger = logging.getLogger(__name__)

router = APIRouter()

# The read scopes the chart tools need (mirrors api-collection "Get a token").
DEFAULT_SCOPE = (
    "openid api:fhir user/Patient.read user/Condition.read user/Encounter.read "
    "user/MedicationRequest.read user/AllergyIntolerance.read "
    "user/Observation.read user/DocumentReference.read"
)

_TRUTHY = {"1", "true", "yes", "on"}


def demo_token_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get("DEMO_TOKEN_ENABLED", "").strip().lower() in _TRUTHY


@dataclass
class DemoTokenConfig:
    token_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    scope: str
    clinician: str
    patient: str  # optional default patient uuid to prefill ("" if unset)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DemoTokenConfig":
        source = os.environ if env is None else env
        base_url, _ = resolve_openemr_urls(source)
        base = (base_url or "http://localhost:8300").rstrip("/")
        token_url = source.get("OPENEMR_OAUTH_TOKEN_URL") or f"{base}/oauth2/default/token"
        return cls(
            token_url=token_url,
            client_id=source.get("DEMO_OAUTH_CLIENT_ID", ""),
            client_secret=source.get("DEMO_OAUTH_CLIENT_SECRET", ""),
            username=source.get("DEMO_OAUTH_USERNAME", "admin"),
            password=source.get("DEMO_OAUTH_PASSWORD", ""),
            scope=source.get("DEMO_OAUTH_SCOPE", DEFAULT_SCOPE),
            clinician=source.get("DEMO_CLINICIAN", "nurse-maria"),
            patient=source.get("DEMO_PATIENT", ""),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.username and self.password)


class DemoTokenResponse(BaseModel):
    access_token: str
    expires_in: int = 0
    clinician: str
    patient: str = ""


class DemoTokenMinter:
    """Performs the OpenEMR password grant. Transport is injectable for tests."""

    def __init__(
        self,
        config: DemoTokenConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.config = config
        self._transport = transport
        self._timeout = timeout_seconds

    async def mint(self) -> DemoTokenResponse:
        data = {
            "grant_type": "password",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "user_role": "users",
            "username": self.config.username,
            "password": self.config.password,
            "scope": self.config.scope,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.post(self.config.token_url, data=data)
        except httpx.HTTPError as exc:
            logger.warning("demo token mint unreachable: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="OpenEMR token endpoint unreachable")

        if response.status_code != 200:
            # Never echo OpenEMR's body — it can carry SQL/debug detail (AUDIT S4).
            logger.warning("demo token mint rejected status=%s", response.status_code)
            raise HTTPException(status_code=502, detail="OpenEMR rejected the token request")

        token = response.json().get("access_token")
        if not token:
            raise HTTPException(status_code=502, detail="OpenEMR returned no access token")

        return DemoTokenResponse(
            access_token=token,
            expires_in=response.json().get("expires_in", 0) or 0,
            clinician=self.config.clinician,
            patient=self.config.patient,
        )


def get_demo_token_minter() -> DemoTokenMinter:
    """FastAPI provider; overridden in tests."""
    return DemoTokenMinter(DemoTokenConfig.from_env())


@router.post("/demo/token", response_model=DemoTokenResponse)
async def demo_token(
    minter: DemoTokenMinter = Depends(get_demo_token_minter),
) -> DemoTokenResponse:
    if not demo_token_enabled():
        # Invisible unless a demo environment explicitly opts in.
        raise HTTPException(status_code=404, detail="not found")
    if not minter.config.configured:
        raise HTTPException(status_code=503, detail="demo token minting is not configured")
    return await minter.mint()
