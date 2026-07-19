"""OAuth2 authorization-code + PKCE — the proper (non-demo) sign-in path.

The browser runs the PKCE dance (verifier/challenge/state, popup to OpenEMR's
authorize page); the sidecar only exposes:

  * GET  /auth/config   — feature discovery: enabled + client_id + authorize_url
  * GET  /auth/callback — static relay page: posts ?code&state to the opener
  * POST /auth/exchange — server-side code->token exchange (no CORS, no secret)

Fail-closed like /demo/token: disabled -> 404, upstream rejection -> 502 with
no OpenEMR response body leaked (AUDIT S4).
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth_pkce import PkceConfig, TokenExchanger, get_token_exchanger
from app.main import create_app

BASE_ENV = {
    "OPENEMR_BASE_URL": "https://emr.example.test",
    "PKCE_CLIENT_ID": "public-client-123",
}


def _client(env: dict | None = None, transport: httpx.MockTransport | None = None) -> TestClient:
    app = create_app()
    config = PkceConfig.from_env(env if env is not None else BASE_ENV)
    app.dependency_overrides[get_token_exchanger] = (
        lambda: TokenExchanger(config, transport=transport)
    )
    return TestClient(app, raise_server_exceptions=False)


# --- /auth/config ------------------------------------------------------------


def test_config_disabled_without_client_id():
    response = _client(env={"OPENEMR_BASE_URL": "https://emr.example.test"}).get("/auth/config")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}


def test_config_enabled_surfaces_authorize_url_and_client_id():
    body = _client().get("/auth/config").json()
    assert body["enabled"] is True
    assert body["client_id"] == "public-client-123"
    assert body["authorize_url"] == "https://emr.example.test/oauth2/default/authorize"
    assert "openid" in body["scope"]
    assert "api:fhir" in body["scope"]


def test_config_never_contains_a_secret():
    body = _client().get("/auth/config").json()
    assert "secret" not in json.dumps(body).lower()


# --- /auth/callback ----------------------------------------------------------


def test_callback_page_relays_to_opener_and_closes():
    response = _client().get("/auth/callback?code=abc&state=xyz")
    assert response.status_code == 200
    page = response.text
    assert "postMessage" in page
    assert "window.opener" in page
    assert "window.close" in page
    # The page must relay only to our own origin — never a wildcard target.
    assert '"*"' not in page


# --- /auth/exchange ----------------------------------------------------------


def _token_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_exchange_returns_access_token():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    response = _client(transport=_token_transport(handler)).post(
        "/auth/exchange",
        json={
            "code": "auth-code-1",
            "code_verifier": "a" * 43,
            "redirect_uri": "https://copilot.example.test/auth/callback",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"access_token": "tok-1", "expires_in": 3600}
    # The exchange hits OpenEMR's token endpoint with the PKCE grant...
    assert captured["url"] == "https://emr.example.test/oauth2/default/token"
    assert captured["body"]["grant_type"] == "authorization_code"
    assert captured["body"]["code"] == "auth-code-1"
    assert captured["body"]["code_verifier"] == "a" * 43
    assert captured["body"]["client_id"] == "public-client-123"
    # ...and with no secret configured none is sent.
    assert "client_secret" not in captured["body"]


def test_exchange_sends_secret_server_side_when_configured():
    # OpenEMR only grants user/* FHIR scopes to confidential clients, so the
    # secret is held by the sidecar and joins the exchange SERVER-SIDE only —
    # the browser still runs plain PKCE and never sees it.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"access_token": "tok-2", "expires_in": 60})

    env = {**BASE_ENV, "PKCE_CLIENT_SECRET": "server-side-secret"}
    response = _client(env=env, transport=_token_transport(handler)).post(
        "/auth/exchange",
        json={"code": "c", "code_verifier": "a" * 43,
              "redirect_uri": "https://copilot.example.test/auth/callback"},
    )
    assert response.status_code == 200
    assert captured["body"]["client_secret"] == "server-side-secret"


def test_config_stays_secretless_even_when_secret_configured():
    env = {**BASE_ENV, "PKCE_CLIENT_SECRET": "server-side-secret"}
    body = _client(env=env).get("/auth/config").json()
    assert "server-side-secret" not in json.dumps(body)
    assert "secret" not in json.dumps(body).lower()


def test_exchange_upstream_rejection_is_502_without_body_leak():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "hint": "SQL-ish internals"})

    response = _client(transport=_token_transport(handler)).post(
        "/auth/exchange",
        json={"code": "bad", "code_verifier": "a" * 43,
              "redirect_uri": "https://copilot.example.test/auth/callback"},
    )
    assert response.status_code == 502
    assert "internals" not in response.text
    assert "invalid_grant" not in response.text


def test_exchange_unreachable_token_endpoint_is_502():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    response = _client(transport=_token_transport(handler)).post(
        "/auth/exchange",
        json={"code": "x", "code_verifier": "a" * 43,
              "redirect_uri": "https://copilot.example.test/auth/callback"},
    )
    assert response.status_code == 502


def test_exchange_missing_fields_is_422():
    response = _client().post("/auth/exchange", json={"code": "only-a-code"})
    assert response.status_code == 422


def test_exchange_disabled_is_404():
    response = _client(env={"OPENEMR_BASE_URL": "https://emr.example.test"}).post(
        "/auth/exchange",
        json={"code": "x", "code_verifier": "a" * 43,
              "redirect_uri": "https://copilot.example.test/auth/callback"},
    )
    assert response.status_code == 404


def test_exchange_empty_upstream_token_is_502():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})

    response = _client(transport=_token_transport(handler)).post(
        "/auth/exchange",
        json={"code": "x", "code_verifier": "a" * 43,
              "redirect_uri": "https://copilot.example.test/auth/callback"},
    )
    assert response.status_code == 502
