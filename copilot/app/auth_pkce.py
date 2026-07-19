"""OAuth2 authorization-code + PKCE — the proper sign-in path.

The browser does the PKCE dance itself: it generates the code_verifier and
S256 challenge, opens OpenEMR's authorize page in a popup, and receives the
authorization code back through ``/auth/callback`` (a static relay page).
The sidecar's only stateful job is the final code->token exchange, done
server-to-server so OpenEMR's token endpoint needs no CORS and the flow works
from any origin the client is registered for.

This is a *public* OAuth client (``token_endpoint_auth_method: none``): there
is no client secret anywhere in this flow, so nothing here is secret-bearing.
Safety rails mirror ``/demo/token``:

  * OFF by default — endpoints 404 unless ``PKCE_CLIENT_ID`` is configured.
  * Leak-free — OpenEMR's raw error bodies never reach the browser (AUDIT S4).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import resolve_openemr_urls
from .demo_token import DEFAULT_SCOPE

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass
class PkceConfig:
    client_id: str
    authorize_url: str
    token_url: str
    scope: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PkceConfig":
        source = os.environ if env is None else env
        base_url, _ = resolve_openemr_urls(source)
        base = (base_url or "http://localhost:8300").rstrip("/")
        return cls(
            client_id=source.get("PKCE_CLIENT_ID", ""),
            authorize_url=source.get("PKCE_AUTHORIZE_URL") or f"{base}/oauth2/default/authorize",
            token_url=source.get("OPENEMR_OAUTH_TOKEN_URL") or f"{base}/oauth2/default/token",
            scope=source.get("PKCE_SCOPE", DEFAULT_SCOPE),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.client_id)


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1)
    code_verifier: str = Field(min_length=43, max_length=128)   # RFC 7636 §4.1
    redirect_uri: str = Field(min_length=1)


class ExchangeResponse(BaseModel):
    access_token: str
    expires_in: int = 0


class TokenExchanger:
    """Performs the authorization-code + PKCE exchange. Transport injectable."""

    def __init__(
        self,
        config: PkceConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.config = config
        self._transport = transport
        self._timeout = timeout_seconds

    async def exchange(self, request: ExchangeRequest) -> ExchangeResponse:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "code": request.code,
            "code_verifier": request.code_verifier,
            "redirect_uri": request.redirect_uri,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.post(self.config.token_url, data=data)
        except httpx.HTTPError as exc:
            logger.warning("pkce exchange unreachable: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="OpenEMR token endpoint unreachable")

        if response.status_code != 200:
            # Never echo OpenEMR's body — it can carry internals (AUDIT S4).
            logger.warning("pkce exchange rejected status=%s", response.status_code)
            raise HTTPException(status_code=502, detail="OpenEMR rejected the token exchange")

        token = response.json().get("access_token")
        if not token:
            raise HTTPException(status_code=502, detail="OpenEMR returned no access token")
        return ExchangeResponse(
            access_token=token,
            expires_in=response.json().get("expires_in", 0) or 0,
        )


def get_token_exchanger() -> TokenExchanger:
    """FastAPI provider; overridden in tests."""
    return TokenExchanger(PkceConfig.from_env())


@router.get("/auth/config")
async def auth_config(
    exchanger: TokenExchanger = Depends(get_token_exchanger),
) -> dict:
    """Feature discovery for the UI. Everything here is public-client metadata."""
    config = exchanger.config
    if not config.enabled:
        return {"enabled": False}
    return {
        "enabled": True,
        "client_id": config.client_id,
        "authorize_url": config.authorize_url,
        "scope": config.scope,
    }


# The relay page runs in the OAuth popup after OpenEMR redirects back. It
# forwards ?code&state to the window that opened it — targeted strictly at our
# own origin, never "*" — and closes. No external assets, no inline data.
_CALLBACK_PAGE = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Signing in…</title></head>
<body style="font-family:sans-serif; color:#5f6b76; padding:2rem;">
Completing sign-in…
<script>
  (function () {
    var params = new URLSearchParams(location.search);
    var payload = {
      type: "copilot-oauth-callback",
      code: params.get("code") || "",
      state: params.get("state") || "",
      error: params.get("error") || ""
    };
    if (window.opener) {
      window.opener.postMessage(payload, location.origin);
      document.body.textContent = "Signed in - you can close this window.";
      window.close();
    } else {
      document.body.textContent = "This page only works as an OAuth popup.";
    }
  })();
</script>
</body>
</html>
"""


@router.get("/auth/callback")
async def auth_callback() -> HTMLResponse:
    """OAuth redirect target: relay the code to the opener and close."""
    return HTMLResponse(_CALLBACK_PAGE, headers={"Cache-Control": "no-cache"})


@router.post("/auth/exchange", response_model=ExchangeResponse)
async def auth_exchange(
    request: ExchangeRequest,
    exchanger: TokenExchanger = Depends(get_token_exchanger),
) -> ExchangeResponse:
    if not exchanger.config.enabled:
        raise HTTPException(status_code=404, detail="not found")
    return await exchanger.exchange(request)
